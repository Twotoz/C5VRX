// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL top level,
// Sipeed Tang Nano 9K (GW1NR-LV9QN88PC6/I5).
//
// Two XIAO ESP32-C5 with the tang9k_dual hardware-experiment firmware
// (hardware_experiments/tang9k_dual/hwx.h) send MODEM_DIAG Q4/I4 ultrafine
// IQ plus their 40 MHz PARLIO RX sample clock. C5 A is the timing master:
// everything runs on its clock. C5 B is aligned to it (align_b.v), both are
// combined (pre-detection MRC) and demodulated (mrc_core.v).
//
// Output, chosen at build time (fpga/build.sh dac|lcd|hdmi; one wiring of
// the C5s serves all three):
//   OUT_DAC  six-bit composite codes at 40 MS/s on Tang pins 33,34,40,35,
//            41,42 (bit 0..5) into the C5VRX resistor network
//            (8.2k/3.9k/2k/1k/470R/240R plus 200R): the goggle decodes.
//   OUT_LCD  40-pin RGB panel (not implemented yet: needs the video decoder).
//   OUT_HDMI HDMI (not implemented yet: needs the video decoder).
// While a C5 shows its menu (iq[7:6] held 0 for a line) its six-bit menu
// raster is passed straight to the output.
//
// Onboard LEDs (active low, bank 3): 0 C5 A clock, 1 C5 B clock, 2 B
// combined (aligned and coherent), 3 menu passthrough, 4 heartbeat, 5 the
// FPGA owns the channel.
// Button S1 (pin 4): reset. Button S2 (pin 3): channel for both C5s, short
// press = next channel, hold = next band, sent over the channel link
// (channel_link_tx.v, C5 side main/channel_link.h; silent until the first
// press). Extends FPGA_IQ, PR #257 and the dual-C5 study.
`default_nettype none
module top (
    input  wire       xtal,        // 27 MHz
    input  wire       btn_n,       // S1, active low: reset
    input  wire       btn2_n,      // S2, active low: channel (short) / band (hold)
    output wire       link_tx,     // channel link to D9 of both C5s, inverted (transistor level shifter)
    input  wire       clk_a,       // C5 A RX40
    input  wire [7:0] iq_a,        // C5 A iq[7:0] = {I sign, I2, I1, I0, Q sign, Q2, Q1, Q0}
    input  wire       clk_b,       // C5 B RX40 (through the 1.8 V divider)
    input  wire [7:0] iq_b,
`ifdef OUT_DAC
    output reg  [5:0] dac,
`endif
    output wire [5:0] led_n
);
    // Capture edges: rising by default; build with -DNEG_A / -DNEG_B if the
    // bench shows the data eye on the other edge.
    reg [7:0] cap_a, cap_b;
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
    reg [7:0] ra;
    always @(posedge clk_a) ra <= cap_a;

    // reset in the A domain: power-on and button
    reg [1:0]  bs = 2'b00;
    reg [11:0] por = 12'd0;
    wire       rst = ~por[11];
    always @(posedge clk_a) begin
        bs <= {bs[0], btn_n};
        if (!bs[1]) por <= 12'd0;
        else if (!por[11]) por <= por + 12'd1;
    end

    wire [7:0] a_al, b_al;
    wire       en_b, b_alive;
    wire [5:0] lag;
    align_b u_align (.clk_b(clk_b), .raw_b_in(cap_b), .clk_a(clk_a), .rst_a(rst), .raw_a(ra),
                     .raw_a_d(a_al), .raw_b(b_al), .en_b(en_b), .b_alive(b_alive), .lag(lag));

    wire [5:0] code;
    mrc_core u_core (.clk(clk_a), .rst(rst), .raw_a(a_al), .raw_b(b_al), .en_b(en_b), .code(code),
                     .w_a(), .w_b(), .theta());

    // menu passthrough: iq[7:6] (I sign, I bit 2) held 0 for 2560 samples
    reg [11:0] za, zb;
    reg        menu_a, menu_b;
    always @(posedge clk_a) begin
        if (a_al[7:6] != 2'b00) begin za <= 0; menu_a <= 1'b0; end
        else if (za != 12'd2560) za <= za + 12'd1;
        else menu_a <= 1'b1;
        if (b_al[7:6] != 2'b00 || !b_alive) begin zb <= 0; menu_b <= 1'b0; end
        else if (zb != 12'd2560) zb <= zb + 12'd1;
        else menu_b <= 1'b1;
    end
    wire [5:0] out_code = menu_a ? a_al[5:0] : menu_b ? b_al[5:0] : code;

`ifdef OUT_DAC
    always @(posedge clk_a) dac <= out_code;
`elsif OUT_LCD
    initial $fatal(1, "OUT_LCD: the video decoder and LCD timing are not implemented yet");
`elsif OUT_HDMI
    initial $fatal(1, "OUT_HDMI: the video decoder and HDMI output are not implemented yet");
`else
    initial $fatal(1, "define OUT_DAC, OUT_LCD or OUT_HDMI");
`endif

    // ---- status LEDs in the 27 MHz domain -------------------------------------
    reg ta = 1'b0, tb = 1'b0;
    always @(posedge clk_a) ta <= ~ta;
    always @(posedge clk_b) tb <= ~tb;
    reg [2:0] sa, sb;
    reg [2:0] s_en, s_menu;
    reg [20:0] cnt_a, cnt_b;
    reg [24:0] beat;
    always @(posedge xtal) begin
        sa <= {sa[1:0], ta};
        sb <= {sb[1:0], tb};
        s_en   <= {s_en[1:0], en_b};
        s_menu <= {s_menu[1:0], menu_a | menu_b};
        cnt_a <= (sa[2] != sa[1]) ? 21'd0 : (cnt_a[20] ? cnt_a : cnt_a + 21'd1);
        cnt_b <= (sb[2] != sb[1]) ? 21'd0 : (cnt_b[20] ? cnt_b : cnt_b + 21'd1);
        beat  <= beat + 25'd1;
    end
    assign led_n = ~{link_valid, beat[24], s_menu[2], s_en[2], ~cnt_b[20], ~cnt_a[20]};

    // ---- channel link (27 MHz domain) -------------------------------------------
    wire [5:0] link_index;
    wire       link_valid;
    channel_buttons #(.CLK_HZ(27000000)) u_btn (.clk(xtal), .btn_n(btn2_n), .index(link_index), .valid(link_valid));
    channel_link_tx #(.CLK_HZ(27000000), .INVERT(1)) u_link (.clk(xtal), .valid(link_valid), .index(link_index),
                                                             .tx(link_tx));
endmodule
`default_nettype wire
