// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL on a Sipeed Tang
// Nano 1K (GW1NZ-LV1QN48C6/I5): two C5s (tang9k_dual firmware), lite B
// alignment (align_lite.v), lite pre-detection MRC (lite_core.v), six-bit
// DAC at 40 MS/s into the C5VRX resistor network. No LCD (its pins carry the
// C5s), no LEDs (their pins carry C5 B), no button: power-on reset. While a
// C5 shows its menu (iq[7:6] held 0 for a line) its raster is passed through.
// -DSINGLE builds the one-C5 comparison (no alignment, B ignored).
`default_nettype none
module top_1k (
    input  wire       clk_a,
    input  wire [7:0] iq_a,
    input  wire       clk_b,
    input  wire [7:0] iq_b,
    output reg  [5:0] dac
);
    reg [7:0] cap_a, cap_b, ra;
`ifdef NEG_A
    always @(negedge clk_a) cap_a <= iq_a;
`else
    always @(posedge clk_a) cap_a <= iq_a;
`endif
`ifdef NEG_B
    always @(negedge clk_b) cap_b <= iq_b;
`else
    always @(posedge clk_b) cap_b <= iq_b;
`endif
    always @(posedge clk_a) ra <= cap_a;

    reg [7:0] por = 8'd0;
    wire      rst = ~por[7];
    always @(posedge clk_a) if (!por[7]) por <= por + 8'd1;

    wire [7:0] a_al, b_al;
    wire       en_b, b_alive;
`ifdef SINGLE
    // one C5 only (comparison build): no alignment, no B
    assign a_al = ra; assign b_al = 8'd0; assign en_b = 1'b0; assign b_alive = 1'b0;
`else
    align_lite u_align (.clk_b(clk_b), .raw_b_in(cap_b), .clk_a(clk_a), .rst_a(rst), .raw_a(ra),
                        .raw_a_d(a_al), .raw_b(b_al), .en_b(en_b), .b_alive(b_alive));
`endif
    wire [5:0] code;
    lite_core u_core (.clk(clk_a), .rst(rst), .raw_a(a_al), .raw_b(b_al), .en_b(en_b), .code(code));

    reg [11:0] za = 0, zb = 0;
    reg        menu_a = 1'b0, menu_b = 1'b0;
    always @(posedge clk_a) begin
        if (a_al[7:6] != 2'b00) begin za <= 0; menu_a <= 1'b0; end
        else if (za != 12'd2560) za <= za + 12'd1;
        else menu_a <= 1'b1;
        if (b_al[7:6] != 2'b00 || !b_alive) begin zb <= 0; menu_b <= 1'b0; end
        else if (zb != 12'd2560) zb <= zb + 12'd1;
        else menu_b <= 1'b1;
        dac <= menu_a ? a_al[5:0] : menu_b ? b_al[5:0] : code;
    end
endmodule
`default_nettype wire
