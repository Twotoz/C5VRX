// C5VRX by Twotoz and the C5VRX contributors: Tripleversity top level,
// Sipeed Tang Nano 20K (GW2AR-LV18QN88C8/I7). Fit-check stage.
//
// Three XIAO ESP32-C5 send the top three bits of each 4-bit MODEM_DIAG lane
// (sign, bit 2, bit 1 of I and Q; STRONG LANE stays on in the C5) plus their
// 40 MHz PARLIO RX clock: 3 x 7 wires on the 3.3 V header. C5 A is the
// timing master; B and C are aligned to it (align_b.v, two instances),
// combined and demodulated (mrc3_core.v). The channel link (pin 52) goes to
// D9 of all three C5s, not inverted (3.3 V bank).
//
// Output: the 40-pin RGB LCD (800x480) needs the video decoder, which is not
// written yet. Until then the six-bit composite code is put on the LCD
// connector's green lines (g[5:0], pins 32..37) so the build fits and times
// the complete fusion path; a bench can probe it there.
// Button S1 (pin 88): reset. S2 (pin 87): channel (short) / band (hold).
`default_nettype none
module top3_20k (
    input  wire       xtal,        // 27 MHz
    input  wire       btn_n,
    input  wire       btn2_n,
    output wire       link_tx,
    input  wire       clk_a, clk_b, clk_c,
    input  wire [5:0] iq_a,        // {I sign, I2, I1, Q sign, Q2, Q1} of the 4-bit lane
    input  wire [5:0] iq_b,
    input  wire [5:0] iq_c,
    output reg  [5:0] lcd_g
);
    reg [5:0] cap_a, cap_b, cap_c, ra;
    always @(posedge clk_a) cap_a <= iq_a;
    always @(posedge clk_b) cap_b <= iq_b;
    always @(posedge clk_c) cap_c <= iq_c;
    always @(posedge clk_a) ra <= cap_a;

    reg [1:0]  bs = 2'b00;
    reg [11:0] por = 12'd0;
    wire       rst = ~por[11];
    always @(posedge clk_a) begin
        bs <= {bs[0], btn_n};
        if (!bs[1]) por <= 12'd0;
        else if (!por[11]) por <= por + 12'd1;
    end

    // align_b correlates 4-bit nibbles: a 3-bit value v enters as {v, 0}
    function automatic [7:0] nib;
        input [5:0] r;
        begin nib = {r[5:3], 1'b0, r[2:0], 1'b0}; end
    endfunction
    wire [7:0] a_d1, a_d2, b_al, c_al;
    wire       en_b, en_c, b_alive, c_alive;
    align_b u_alb (.clk_b(clk_b), .raw_b_in(nib(cap_b)), .clk_a(clk_a), .rst_a(rst), .raw_a(nib(ra)),
                   .raw_a_d(a_d1), .raw_b(b_al), .en_b(en_b), .b_alive(b_alive), .lag());
    align_b u_alc (.clk_b(clk_c), .raw_b_in(nib(cap_c)), .clk_a(clk_a), .rst_a(rst), .raw_a(nib(ra)),
                   .raw_a_d(a_d2), .raw_b(c_al), .en_b(en_c), .b_alive(c_alive), .lag());

    // DVR mode (C5 C leaves the combiner): set by the menu later
    reg dvr = 1'b0;
    wire [5:0] code;
    mrc3_core u_core (.clk(clk_a), .rst(rst),
                      .raw_a({a_d1[7:5], a_d1[3:1]}), .raw_b({b_al[7:5], b_al[3:1]}), .raw_c({c_al[7:5], c_al[3:1]}),
                      .en_b(en_b), .en_c(en_c & ~dvr), .code(code));
    always @(posedge clk_a) lcd_g <= code;

    wire [5:0] link_index;
    wire       link_valid;
    channel_buttons #(.CLK_HZ(27000000)) u_btn (.clk(xtal), .btn_n(btn2_n), .index(link_index), .valid(link_valid));
    channel_link_tx #(.CLK_HZ(27000000), .INVERT(0)) u_link (.clk(xtal), .valid(link_valid), .index(link_index),
                                                             .tx(link_tx));
endmodule
`default_nettype wire
