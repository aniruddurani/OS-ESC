// Logical-datagram access: plans the datagram over the FMMUs (fmmu_plan), then walks
// the datagram's byte offsets ahead of the byte stream (fmmu_walk) into a small queue,
// starts the memory reads early and holds their data until the byte that needs it
// passes.
//
//   flush          a new datagram starts (dg_start): drop everything
//   plan_len       the datagram's length is known (dg_len_done of a logical command)
//   consume        a data byte of the datagram is passing: take the head entry
//   head_*         what the head entry says about that byte: read hit and the data, write hit
//                  and its physical address
//   rd_req/rd_ack  the next memory read; the engine's memory port gives write priority, so a
//                  request is repeated until `rd_ack`
//
// Ordering: bytes are processed in order. A read of offset q whose address equals that of a
// write by an older, not yet consumed offset waits until it is the head, so it sees that
// write exactly as if the bytes were handled one by one, whatever the byte spacing.
module dg_access #(
    parameter N_FMMU    = 3,        // 0..16
    parameter RAM_BYTES = 1024
) (
    input         clk,
    input         rst,
    input  [8*16*((N_FMMU == 0) ? 1 : N_FMMU)-1:0] cfg,

    input         flush,
    input         plan_len,
    input  [31:0] dg_addr,
    input  [10:0] dg_len,
    input         is_rd,
    input         is_wr,

    output        rd_req,
    output [15:0] rd_req_addr,
    input         rd_ack,
    input         mem_rvalid,
    input  [7:0]  mem_rdata,

    input         consume,
    output        head_rd_hit,
    output [7:0]  head_rd_data,
    output        head_wr_hit,
    output [15:0] head_wr_addr
);
    localparam NF = (N_FMMU == 0) ? 1 : N_FMMU;
    localparam NQ = 4;

    // --- plan and walk -------------------------------------------------------
    wire          plan_done;
    wire [NF-1:0] hr, hw;
    wire [11*NF-1:0] seg_start, seg_end;
    wire [16*NF-1:0] seg_base;

    fmmu_plan #(.N_FMMU(N_FMMU), .RAM_BYTES(RAM_BYTES)) u_plan (
        .clk(clk), .rst(rst), .cfg(cfg),
        .plan_len(plan_len), .dg_addr(dg_addr), .dg_len(dg_len), .is_rd(is_rd), .is_wr(is_wr),
        .plan_done(plan_done), .hr(hr), .hw(hw),
        .seg_start(seg_start), .seg_end(seg_end), .seg_base(seg_base)
    );

    // walker: one offset per clock while the queue has room
    reg        armed;
    reg [10:0] wo;
    reg [2:0]  occ;             // entries requested or held
    wire       go      = plan_done || armed;
    wire       req_now = go && (occ < NQ) && (wo != dg_len);

    wire        res_valid, w_rd_hit, w_wr_hit;
    wire [10:0] res_off_unused;
    wire [15:0] w_rd_addr, w_wr_addr;

    fmmu_walk #(.N_FMMU(N_FMMU)) u_walk (
        .clk(clk), .rst(rst),
        .hr(hr), .hw(hw), .seg_start(seg_start), .seg_end(seg_end), .seg_base(seg_base),
        .req_valid(req_now), .req_off(wo),
        .res_valid(res_valid), .res_off(res_off_unused),
        .rd_hit(w_rd_hit), .rd_addr(w_rd_addr), .wr_hit(w_wr_hit), .wr_addr(w_wr_addr)
    );
    wire _unused_ok = &{1'b0, res_off_unused};

    // --- queue ---------------------------------------------------------------
    reg        e_v   [0:NQ-1];  // result arrived, not yet consumed
    reg        e_rh  [0:NQ-1];
    reg        e_wh  [0:NQ-1];
    reg        e_haz [0:NQ-1];  // read must wait until this entry is the head
    reg        e_iss [0:NQ-1];  // read issued
    reg        e_rdy [0:NQ-1];  // read data held (or no read)
    reg [15:0] e_ra  [0:NQ-1];
    reg [15:0] e_wa  [0:NQ-1];
    reg [7:0]  e_d   [0:NQ-1];

    reg [2:0] wr_ptr;           // next result goes here (low 2 bits index, bit 2 counts wraps)
    reg [2:0] rd_ptr;           // head: the next byte to pass
    reg [2:0] is_ptr;           // next read to issue
    reg [2:0] rcv_ptr;          // next read whose data is due

    assign head_rd_hit  = e_rh[rd_ptr[1:0]];
    assign head_rd_data = e_d[rd_ptr[1:0]];
    assign head_wr_hit  = e_wh[rd_ptr[1:0]];
    assign head_wr_addr = e_wa[rd_ptr[1:0]];

    assign rd_req = (is_ptr != wr_ptr) && e_v[is_ptr[1:0]] && e_rh[is_ptr[1:0]] && !e_iss[is_ptr[1:0]] &&
                    (!e_haz[is_ptr[1:0]] || (is_ptr == rd_ptr));
    assign rd_req_addr = e_ra[is_ptr[1:0]];

    // does an older, unconsumed entry write the byte this result reads?
    reg haz_n;
    integer j;
    always @* begin
        haz_n = 1'b0;
        for (j = 0; j < NQ; j = j + 1)
            if (e_v[j] && e_wh[j] && (e_wa[j] == w_rd_addr)) haz_n = 1'b1;
    end

    integer i;
    always @(posedge clk) begin
        // walker
        if (plan_done) armed <= 1'b1;
        if (go && (wo == dg_len)) armed <= 1'b0;
        if (req_now) wo <= wo + 11'd1;
        occ <= occ + {2'd0, req_now} - {2'd0, consume};

        // a walk result becomes the newest entry
        if (res_valid) begin
            e_v[wr_ptr[1:0]]   <= 1'b1;
            e_rh[wr_ptr[1:0]]  <= w_rd_hit;
            e_wh[wr_ptr[1:0]]  <= w_wr_hit;
            e_ra[wr_ptr[1:0]]  <= w_rd_addr;
            e_wa[wr_ptr[1:0]]  <= w_wr_addr;
            e_haz[wr_ptr[1:0]] <= w_rd_hit && haz_n;
            e_iss[wr_ptr[1:0]] <= 1'b0;
            e_rdy[wr_ptr[1:0]] <= !w_rd_hit;
            wr_ptr        <= wr_ptr + 3'd1;
        end

        // read issue; entries without a read, and consumed ones, are passed over
        if (rd_ack) begin
            e_iss[is_ptr[1:0]] <= 1'b1;
            is_ptr        <= is_ptr + 3'd1;
        end else if ((is_ptr != wr_ptr) && (!e_v[is_ptr[1:0]] || !e_rh[is_ptr[1:0]])) begin
            is_ptr <= is_ptr + 3'd1;
        end

        // read data comes back in issue order
        if (mem_rvalid) begin
            e_d[rcv_ptr[1:0]]   <= mem_rdata;
            e_rdy[rcv_ptr[1:0]] <= 1'b1;
            rcv_ptr        <= rcv_ptr + 3'd1;
        end else if ((rcv_ptr != wr_ptr) && (!e_v[rcv_ptr[1:0]] || !e_rh[rcv_ptr[1:0]])) begin
            rcv_ptr <= rcv_ptr + 3'd1;
        end

        // the head byte passes
        if (consume) begin
            e_v[rd_ptr[1:0]] <= 1'b0;
            rd_ptr      <= rd_ptr + 3'd1;
        end

        if (flush || rst) begin
            armed   <= 1'b0;
            wo      <= 11'd0;
            occ     <= 3'd0;
            wr_ptr  <= 3'd0;
            rd_ptr  <= 3'd0;
            is_ptr  <= 3'd0;
            rcv_ptr <= 3'd0;
            for (i = 0; i < NQ; i = i + 1) begin
                e_v[i]   <= 1'b0;
                e_rh[i]  <= 1'b0;
                e_wh[i]  <= 1'b0;
                e_haz[i] <= 1'b0;
                e_iss[i] <= 1'b0;
                e_rdy[i] <= 1'b0;
                e_ra[i]  <= 16'd0;
                e_wa[i]  <= 16'd0;
                e_d[i]   <= 8'd0;
            end
        end
    end

    // Sticky simulation check: a byte passed before its read data was held. The engine
    // relies on the byte spacing (at least 6 clocks); the testbenches read this flag
    // (u_exec.u_acc.late) after every run.
    /* verilator lint_off UNUSEDSIGNAL */
    reg late;
    /* verilator lint_on UNUSEDSIGNAL */
    always @(posedge clk) begin
        if (rst) late <= 1'b0;
        else if (consume && !(e_v[rd_ptr[1:0]] && e_rdy[rd_ptr[1:0]])) late <= 1'b1;
    end
endmodule
