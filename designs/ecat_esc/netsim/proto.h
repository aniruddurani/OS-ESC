// Messages between the Verilator bench and the Python network. Twin of proto.py: change
// both together. On the pipe a message is a 4-byte little-endian length (of the type byte
// and payload), a 1-byte type, and the payload, all little-endian. SEND and REPLY end with
// the frame's bytes.
#pragma once
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>

namespace proto {

enum Type : uint8_t {
    CONFIG = 1, SEND = 2, RUN = 3, REPLY = 4, STATUS = 5, EVENT = 6, QUIT = 7, DUMP = 8,
    SENT = 9
};

// STATUS seen/sticky bits
enum Flag : uint8_t { RX_OVERFLOW = 1, TX_OVERFLOW = 2, TX_UNDERRUN = 4, LATE = 8 };
// EVENT block and kind
enum Block : uint8_t {
    MII_RX = 1, RX_FIFO = 2, PARSER = 3, EXEC = 4, FMMU = 5, MEM = 6, TX_FIFO = 7, MII_TX = 8
};
enum Kind : uint8_t {
    FRAME_START = 1, FRAME_END = 2, DG_HDR = 3, FMMU_HIT = 4, MEM_RD = 5, MEM_WR = 6,
    WKC = 7, FLAG = 8, FAULT = 9
};
// FAULT event a bits
enum Fault : uint32_t { F_RUNT = 1, F_OVERSIZE = 2, F_NON_ETHERCAT = 4, F_MALFORMED = 8, F_RX_ERR = 16 };

struct Config {
    uint64_t seed;
    double rx_ppm, tx_ppm, drift_ppm_per_s, rx_phase_ns, tx_phase_ns, core_phase_ns;
    uint8_t event_level;   // 0 none, 1 sampled, 2 all
};

// Little-endian writer for one message body.
struct Out {
    std::vector<uint8_t> b;
    template <typename T> void put(T v) {
        uint8_t raw[sizeof(T)];
        std::memcpy(raw, &v, sizeof(T));         // the bench runs on little-endian hosts
        b.insert(b.end(), raw, raw + sizeof(T));
    }
    void bytes(const std::vector<uint8_t>& d) { b.insert(b.end(), d.begin(), d.end()); }
};

// Little-endian reader over one message body (type byte already removed).
struct In {
    const std::vector<uint8_t>& b;
    size_t pos = 0;
    explicit In(const std::vector<uint8_t>& body) : b(body) {}
    template <typename T> T get() {
        if (pos + sizeof(T) > b.size()) throw std::runtime_error("message payload too short");
        T v;
        std::memcpy(&v, b.data() + pos, sizeof(T));
        pos += sizeof(T);
        return v;
    }
    std::vector<uint8_t> rest() { std::vector<uint8_t> r(b.begin() + pos, b.end()); pos = b.size(); return r; }
    void done() const { if (pos != b.size()) throw std::runtime_error("message payload too long"); }
};

}  // namespace proto
