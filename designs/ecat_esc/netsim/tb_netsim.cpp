// Network-simulation bench for esc_top: the three clocks at their real ratio, a PHY receive
// driver and a PHY transmit monitor, and the message loop of proto.h on stdin/stdout.
// The Python side (link.py) sends CONFIG first, then SENDs and RUNs. The bench writes only
// while handling RUN or DUMP, and each of those ends with exactly one STATUS, so neither
// side can block the other.
#include <unistd.h>

#include <cmath>
#include <cstdio>
#include <deque>
#include <memory>
#include <stdexcept>
#include <vector>

#include "Vesc_top.h"
#include "Vesc_top___024root.h"
#include "proto.h"
#include "verilated.h"

// Probed internal signals (probes.vlt); a renamed RTL signal fails the build here.
#define TOP(n) (top_->rootp->esc_top__DOT__##n)
#define CORE(n) (top_->rootp->esc_top__DOT__u_core__DOT__##n)
#define PARSE(n) (top_->rootp->esc_top__DOT__u_core__DOT__u_parse__DOT__##n)
#define ACC(n) (top_->rootp->esc_top__DOT__u_core__DOT__u_exec__DOT__u_acc__DOT__##n)

namespace {

FILE* out = nullptr;            // the real stdout; fd 1 is pointed at stderr so RTL output cannot reach the pipe

void write_msg(uint8_t type, const proto::Out& o) {
    uint32_t len = static_cast<uint32_t>(o.b.size() + 1);
    fwrite(&len, 4, 1, out);
    fputc(type, out);
    if (!o.b.empty()) fwrite(o.b.data(), 1, o.b.size(), out);
}

bool read_exact(uint8_t* p, size_t n) {
    while (n) {
        ssize_t r = read(0, p, n);
        if (r <= 0) return false;
        p += r;
        n -= static_cast<size_t>(r);
    }
    return true;
}

// false at a clean end of input
bool read_msg(uint8_t& type, std::vector<uint8_t>& body) {
    uint8_t head[4];
    if (!read_exact(head, 4)) return false;
    uint32_t len;
    std::memcpy(&len, head, 4);
    if (len < 1) throw std::runtime_error("message length 0");
    std::vector<uint8_t> raw(len);
    if (!read_exact(raw.data(), len)) throw std::runtime_error("input ended inside a message");
    type = raw[0];
    body.assign(raw.begin() + 1, raw.end());
    return true;
}

constexpr double MII_PERIOD_PS = 40000.0;   // 25 MHz
constexpr uint64_t CORE_HALF_PS = 5000;     // 100 MHz

// A PHY clock: nominal 25 MHz off by `ppm`, which walks by `drift` ppm per simulated second
// and reflects at +-bound (the configured offset's size).
struct PhyClock {
    double ppm = 0, bound = 0, drift = 0;
    int dir = 1;
    uint64_t next = 0, half = 20000;
    bool level = false;
    void set_period() {
        uint64_t period = 2 * static_cast<uint64_t>(std::llround(MII_PERIOD_PS * (1 + ppm * 1e-6) / 2));
        half = period / 2;
        if (drift != 0) {
            ppm += dir * drift * static_cast<double>(period) * 1e-12;
            if (ppm > bound) { ppm = 2 * bound - ppm; dir = -1; }
            if (ppm < -bound) { ppm = -2 * bound - ppm; dir = 1; }
        }
    }
};

struct RxFrame {
    std::vector<uint8_t> nibs;   // preamble, SFD, data low nibble first
    int er = -1;                 // nibble index (into nibs) that raises rx_er
    unsigned ifg = 24;
    bool record = false;         // emit this frame's events at event level 1
};

struct Ev {
    uint64_t t;
    uint8_t block, kind;
    uint32_t a, b;
    uint64_t seq;                // receive frame count when the event happened
};

constexpr uint64_t RING_FRAMES = 64;

class Bench {
public:
    explicit Bench(VerilatedContext* ctx) : ctx_(ctx), top_(new Vesc_top{ctx}) {}

    void configure(const proto::Config& c) {
        rx_.ppm = c.rx_ppm; rx_.bound = std::fabs(c.rx_ppm); rx_.drift = c.drift_ppm_per_s;
        tx_.ppm = c.tx_ppm; tx_.bound = std::fabs(c.tx_ppm); tx_.drift = c.drift_ppm_per_s;
        rx_.next = static_cast<uint64_t>(std::llround(c.rx_phase_ns * 1000));
        tx_.next = static_cast<uint64_t>(std::llround(c.tx_phase_ns * 1000));
        core_next_ = static_cast<uint64_t>(std::llround(c.core_phase_ns * 1000));
        rx_.set_period();
        tx_.set_period();
        level_ = c.event_level;
        top_->rst = 1;
        top_->rx_dv = 0; top_->rx_er = 0; top_->rxd = 0;
        top_->clk = 0; top_->rx_clk = 0; top_->tx_clk = 0;
        top_->eval();
        uint64_t c0 = cycles_;
        while (cycles_ < c0 + 20) step();
        top_->rst = 0;
        while (cycles_ < c0 + 40) step();
        configured_ = true;
    }

    void queue(RxFrame f) { rxq_.push_back(std::move(f)); }

    // Run until `want` replies came back with nothing left to send, or `timeout` ps passed.
    // want == 0 runs the whole timeout.
    void run(uint32_t want, uint64_t timeout) {
        uint64_t t0 = now_;
        replies_ = 0;
        seen_ = 0;
        emitting_ = true;
        while (now_ - t0 < timeout) {
            if (want && replies_ >= want && rx_idle()) break;
            step();
        }
        emitting_ = false;
        if (rx_idle() && !tx_active_) {          // a frame the RTL dropped must not keep recording on
            inflight_.clear();
            recording_ = 0;
        }
        status();
    }

    // The ring buffer of the last RING_FRAMES frames' events (event level 1), then STATUS.
    void dump() {
        for (const Ev& e : ring_) write_event(e);
        status();
    }

    void status() {
        proto::Out o;
        o.put<uint64_t>(now_); o.put<uint64_t>(cycles_); o.put<uint8_t>(seen_); o.put<uint8_t>(sticky_);
        write_msg(proto::STATUS, o);
        fflush(out);
    }

    bool configured() const { return configured_; }

private:
    bool rx_idle() const { return !in_frame_ && gap_ == 0 && rxq_.empty(); }

    static void write_event(const Ev& e) {
        proto::Out o;
        o.put<uint64_t>(e.t); o.put<uint8_t>(e.block); o.put<uint8_t>(e.kind);
        o.put<uint32_t>(e.a); o.put<uint32_t>(e.b);
        write_msg(proto::EVENT, o);
    }

    void emit(uint8_t block, uint8_t kind, uint32_t a = 0, uint32_t b = 0) {
        if (!level_ || !emitting_) return;
        Ev e{now_, block, kind, a, b, rx_seq_};
        if (level_ == 2 || recording_ > 0) write_event(e);
        if (level_ == 1) {
            ring_.push_back(e);
            while (!ring_.empty() && ring_.front().seq + RING_FRAMES <= rx_seq_) ring_.pop_front();
        }
    }

    // core-domain probes, after a rising edge of clk
    void probe_core() {
        if (TOP(in_v)) {
            if (TOP(in_d) & 0x100) emit(proto::RX_FIFO, proto::FRAME_START);
            if (TOP(in_d) & 0x200) emit(proto::RX_FIFO, proto::FRAME_END);
        }
        if (PARSE(dg_hdr_done))
            emit(proto::PARSER, proto::DG_HDR, PARSE(dg_cmd) | (uint32_t(PARSE(dg_len)) << 8), PARSE(dg_addr));
        if (PARSE(ev_end)) {
            emit(proto::PARSER, proto::FRAME_END);
            uint32_t f = (PARSE(ev_runt) ? proto::F_RUNT : 0) | (PARSE(ev_oversize) ? proto::F_OVERSIZE : 0) |
                         (PARSE(ev_non_ethercat) ? proto::F_NON_ETHERCAT : 0) |
                         (PARSE(ev_malformed) ? proto::F_MALFORMED : 0) | (PARSE(ev_rx_err) ? proto::F_RX_ERR : 0);
            if (f) emit(proto::PARSER, proto::FAULT, f);
        }
        if (ACC(plan_done) && (ACC(hr) | ACC(hw))) emit(proto::FMMU, proto::FMMU_HIT, ACC(hr), ACC(hw));
        if (CORE(mem_req)) emit(proto::MEM, CORE(mem_we) ? proto::MEM_WR : proto::MEM_RD, CORE(mem_addr));
        if (CORE(ev_dg)) emit(proto::EXEC, proto::WKC, CORE(ev_wkc_inc));
        if (TOP(c_valid)) {
            if (TOP(c_first)) emit(proto::TX_FIFO, proto::FRAME_START);
            if (TOP(c_last)) emit(proto::TX_FIFO, proto::FRAME_END);
        }
    }

    // receive-domain probes, after a rising edge of rx_clk
    void probe_rx() {
        if (TOP(r_valid) && TOP(r_first)) emit(proto::MII_RX, proto::FRAME_START);
        if (TOP(r_valid) && TOP(r_last)) emit(proto::MII_RX, proto::FRAME_END);
    }

    void step() {
        uint64_t t = std::min(core_next_, std::min(rx_.next, tx_.next));
        bool core_e = core_next_ == t, rx_e = rx_.next == t, tx_e = tx_.next == t;
        now_ = t;
        ctx_->time(t);
        bool core_rise = core_e && !top_->clk;
        bool rx_fall = rx_e && rx_.level;
        bool rx_rise = rx_e && !rx_.level;
        bool tx_rise = tx_e && !tx_.level;
        if (tx_rise) monitor();              // the transmit pins as they were before this edge
        if (rx_fall) drive(t);               // new receive nibble, sampled at the next rising edge
        if (core_e) { top_->clk = !top_->clk; core_next_ = t + CORE_HALF_PS; }
        if (rx_e) {
            rx_.level = !rx_.level; top_->rx_clk = rx_.level;
            if (rx_.level) rx_.set_period();
            rx_.next = t + rx_.half;
        }
        if (tx_e) {
            tx_.level = !tx_.level; top_->tx_clk = tx_.level;
            if (tx_.level) tx_.set_period();
            tx_.next = t + tx_.half;
        }
        top_->eval();
        if (core_rise) {
            cycles_++;
            if (level_) probe_core();
        }
        if (rx_rise && level_) probe_rx();
        uint8_t f = (top_->rx_overflow ? proto::RX_OVERFLOW : 0) | (top_->tx_overflow ? proto::TX_OVERFLOW : 0) |
                    (top_->tx_underrun ? proto::TX_UNDERRUN : 0) | (ACC(late) ? proto::LATE : 0);
        seen_ |= f;
        sticky_ |= f;
    }

    void drive(uint64_t t) {
        if (!in_frame_ && gap_ > 0) {
            gap_--;
        } else if (!in_frame_ && !rxq_.empty()) {
            cur_ = std::move(rxq_.front());
            rxq_.pop_front();
            in_frame_ = true;
            pos_ = 0;
            sent_start_ = t;
            rx_seq_++;
            inflight_.push_back(cur_.record);
            if (cur_.record) recording_++;
        }
        if (in_frame_ && pos_ < cur_.nibs.size()) {
            top_->rx_dv = 1;
            top_->rxd = cur_.nibs[pos_];
            top_->rx_er = static_cast<int>(pos_) == cur_.er;
            pos_++;
            return;
        }
        top_->rx_dv = 0; top_->rx_er = 0; top_->rxd = 0;
        if (in_frame_) {
            in_frame_ = false;
            gap_ = cur_.ifg > 0 ? cur_.ifg - 1 : 0;      // this idle nibble is the first of the gap
            proto::Out o;
            o.put<uint32_t>(sent_index_++); o.put<uint64_t>(sent_start_); o.put<uint64_t>(t);
            write_msg(proto::SENT, o);
        }
    }

    void monitor() {
        if (top_->tx_en) {
            if (!tx_active_) {
                tx_active_ = true; tx_start_ = now_; tx_nibs_.clear();
                emit(proto::MII_TX, proto::FRAME_START);
            }
            tx_nibs_.push_back(top_->txd & 0xF);
            return;
        }
        if (!tx_active_) return;
        tx_active_ = false;
        emit(proto::MII_TX, proto::FRAME_END);
        if (!inflight_.empty()) {
            if (inflight_.front() && recording_ > 0) recording_--;
            inflight_.pop_front();
        }
        // preamble nibbles 0x5 (at least two) then the SFD nibble 0xD, then data low nibble first
        std::vector<uint8_t> data;
        size_t sfd = 0;
        while (sfd < tx_nibs_.size() && tx_nibs_[sfd] == 0x5) sfd++;
        if (sfd >= 2 && sfd < tx_nibs_.size() && tx_nibs_[sfd] == 0xD) {
            for (size_t i = sfd + 1; i + 1 < tx_nibs_.size(); i += 2)
                data.push_back(static_cast<uint8_t>(tx_nibs_[i] | (tx_nibs_[i + 1] << 4)));
        }
        proto::Out o;
        o.put<uint64_t>(tx_start_); o.put<uint64_t>(now_); o.bytes(data);
        write_msg(proto::REPLY, o);
        replies_++;
    }

    VerilatedContext* ctx_;
    std::unique_ptr<Vesc_top> top_;
    PhyClock rx_, tx_;
    uint64_t core_next_ = 0, now_ = 0, cycles_ = 0;
    bool configured_ = false;
    std::deque<RxFrame> rxq_;
    RxFrame cur_;
    bool in_frame_ = false;
    size_t pos_ = 0;
    unsigned gap_ = 0;
    uint32_t sent_index_ = 0;
    uint64_t sent_start_ = 0;
    bool tx_active_ = false;
    uint64_t tx_start_ = 0;
    std::vector<uint8_t> tx_nibs_;
    uint32_t replies_ = 0;
    uint8_t seen_ = 0, sticky_ = 0;
    uint8_t level_ = 0;          // 0 none, 1 recorded frames plus the ring, 2 all
    bool emitting_ = false;      // only while handling RUN
    uint64_t rx_seq_ = 0;
    int recording_ = 0;
    std::deque<bool> inflight_;  // per frame between receive start and transmit end: recorded?
    std::deque<Ev> ring_;
};

RxFrame make_frame(proto::In& in) {
    RxFrame f;
    f.ifg = in.get<uint16_t>();
    int32_t er = in.get<int32_t>();
    f.record = in.get<uint8_t>() & 1;
    std::vector<uint8_t> data = in.rest();
    for (int i = 0; i < 15; i++) f.nibs.push_back(0x5);
    f.nibs.push_back(0xD);
    for (uint8_t b : data) { f.nibs.push_back(b & 0xF); f.nibs.push_back(b >> 4); }
    f.er = er < 0 ? -1 : er + 16;                        // counted from the first data nibble
    return f;
}

}  // namespace

int main(int argc, char** argv) {
    out = fdopen(dup(1), "wb");
    dup2(2, 1);
    static char buf[1 << 16];
    setvbuf(out, buf, _IOFBF, sizeof buf);

    auto ctx = std::make_unique<VerilatedContext>();
    ctx->commandArgs(argc, argv);
    Bench bench(ctx.get());
    try {
        uint8_t type;
        std::vector<uint8_t> body;
        while (read_msg(type, body)) {
            proto::In in(body);
            if (type != proto::CONFIG && type != proto::QUIT && !bench.configured())
                throw std::runtime_error("message before CONFIG");
            switch (type) {
            case proto::CONFIG: {
                proto::Config c;
                c.seed = in.get<uint64_t>();
                c.rx_ppm = in.get<double>(); c.tx_ppm = in.get<double>(); c.drift_ppm_per_s = in.get<double>();
                c.rx_phase_ns = in.get<double>(); c.tx_phase_ns = in.get<double>(); c.core_phase_ns = in.get<double>();
                c.event_level = in.get<uint8_t>();
                in.done();
                bench.configure(c);
                break;
            }
            case proto::SEND: bench.queue(make_frame(in)); break;
            case proto::RUN: {
                uint32_t want = in.get<uint32_t>();
                uint64_t timeout = in.get<uint64_t>();
                in.done();
                bench.run(want, timeout);
                break;
            }
            case proto::DUMP: in.done(); bench.dump(); break;
            case proto::QUIT: return 0;
            default: throw std::runtime_error("unexpected message type " + std::to_string(type));
            }
        }
    } catch (const std::exception& e) {
        fprintf(stderr, "tb_netsim: %s\n", e.what());
        return 3;
    }
    return 0;
}
