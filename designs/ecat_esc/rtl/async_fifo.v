// Asynchronous FIFO for crossing between two clock domains.
//
// Gray-coded read and write pointers cross through two-flop synchronizers.
// Show-ahead read: `rdata` is the head entry whenever `empty` is low; pulse
// `re` for one rclk to pop it. A write while `full` is dropped. Reset both
// sides together (wrst and rrst asserted at the same time, each long enough
// for its own clock to see it).
module async_fifo #(
    parameter W     = 10,
    parameter DEPTH = 16        // power of two, at least 4
) (
    input              wclk,
    input              wrst,
    input              we,
    input      [W-1:0] wdata,
    output             full,

    input              rclk,
    input              rrst,
    input              re,
    output     [W-1:0] rdata,
    output             empty
);
    localparam AW = $clog2(DEPTH);

    reg [W-1:0] mem [0:DEPTH-1];

    reg [AW:0] wbin, wgray;
    reg [AW:0] rbin, rgray;
    reg [AW:0] rgray_w1, rgray_w2;    // read pointer seen from the write side
    reg [AW:0] wgray_r1, wgray_r2;    // write pointer seen from the read side

    wire do_write = we && !full;
    wire do_read  = re && !empty;

    wire [AW:0] wbin_next = wbin + {{AW{1'b0}}, do_write};
    wire [AW:0] rbin_next = rbin + {{AW{1'b0}}, do_read};

    always @(posedge wclk) begin
        if (wrst) begin
            wbin     <= {(AW + 1){1'b0}};
            wgray    <= {(AW + 1){1'b0}};
            rgray_w1 <= {(AW + 1){1'b0}};
            rgray_w2 <= {(AW + 1){1'b0}};
        end else begin
            wbin     <= wbin_next;
            wgray    <= (wbin_next >> 1) ^ wbin_next;
            rgray_w1 <= rgray;
            rgray_w2 <= rgray_w1;
        end
        if (do_write) mem[wbin[AW-1:0]] <= wdata;
    end

    always @(posedge rclk) begin
        if (rrst) begin
            rbin     <= {(AW + 1){1'b0}};
            rgray    <= {(AW + 1){1'b0}};
            wgray_r1 <= {(AW + 1){1'b0}};
            wgray_r2 <= {(AW + 1){1'b0}};
        end else begin
            rbin     <= rbin_next;
            rgray    <= (rbin_next >> 1) ^ rbin_next;
            wgray_r1 <= wgray;
            wgray_r2 <= wgray_r1;
        end
    end

    // Full: the write pointer has wrapped once more than the (synchronized) read
    // pointer, i.e. the two top Gray bits differ and the rest are equal.
    assign full  = (wgray == {~rgray_w2[AW:AW-1], rgray_w2[AW-2:0]});
    assign empty = (rgray == wgray_r2);
    assign rdata = mem[rbin[AW-1:0]];
endmodule
