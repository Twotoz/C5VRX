// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL lite combiner for
// the Tang Nano 1K (GW1NZ-1: 1152 LUT4, 4 block RAMs, no multipliers).
// The pre-detection MRC of mrc_core.v computed in the angle/log domain with
// tables, no multiplier and no CORDIC; bit-exact with
// fpga/model/lite_model.py (whose docstring holds the arithmetic;
// fpga/sim/run_lite_tb.py checks it). One sample per clock (C5 A's RX40);
// raw_b aligned to raw_a. Block RAM: th_rom (1024 x 8), t2_rom (2048 x 9).
`default_nettype none
module lite_core (
    input  wire       clk,
    input  wire       rst,
    input  wire [7:0] raw_a,        // {I[3:0], Q[3:0]}, sign bit 3
    input  wire [7:0] raw_b,
    input  wire       en_b,
    output reg  [5:0] code
);
`include "lite_tables.vh"
    reg [7:0] th_rom [0:1023];
    reg [8:0] t2_rom [0:2047];
    reg [14:0] t1_rom [0:255];       // {lg, ang}: read by A and B (dual-port block RAM)
`include "lite_rom_init.vh"


    // ---- C0 / C1: T1 lookups (block RAM, registered read) ---------------------
    reg [9:0]  aa1, ab1;
    reg [4:0]  la1, lb1;
    reg        run1;
    always @(posedge clk) begin
        {la1, aa1} <= t1_rom[raw_a];
        {lb1, ab1} <= t1_rom[raw_b];
        run1 <= ~rst;
    end

    // ---- C2: state stage ---------------------------------------------------
    reg  [10:0] mea_acc, meb_acc;    // ~64 E[lg]
    reg  [13:0] vea_acc, veb_acc;    // ~64 E[(lg - me)^2]
    reg  signed [13:0] xr, xi;       // ~128 E[unit phasor (angA - angB)], Q6
    wire [4:0]  mea = mea_acc[10:6], meb = meb_acc[10:6];
    wire [5:0]  vea = (vea_acc[13:6] > 8'd63) ? 6'd63 : vea_acc[11:6];
    wire [5:0]  veb = (veb_acc[13:6] > 8'd63) ? 6'd63 : veb_acc[11:6];

    // normalise the tracker to 5 + 5 bits (shared shift) -> theta table.
    // x >> s fits -16..15 exactly when bits 13..4+s all equal the sign.
    wire [12:4] eqx, eqy;
    genvar g;
    generate
        for (g = 4; g <= 12; g = g + 1) begin : eqb
            assign eqx[g] = xr[g] == xr[13];
            assign eqy[g] = xi[g] == xi[13];
        end
    endgenerate
    wire [12:4] eq = eqx & eqy;
    reg [3:0] sh;
    integer k, j;
    reg ok;
    always @(*) begin
        sh = 4'd9;
        for (k = 8; k >= 0; k = k - 1) begin
            ok = 1'b1;
            for (j = 12; j >= 4; j = j - 1)
                if (j >= 4 + k) ok = ok & eq[j];
            if (ok) sh = k[3:0];
        end
    end
    wire signed [13:0] nxs = xr >>> sh, nys = xi >>> sh;
    reg  [7:0] th_q;
    always @(posedge clk) th_q <= th_rom[{nxs[4:0], nys[4:0]}];

    // combine address
    wire [9:0]        d10 = ab1 + {th_q, 2'b00} - aa1;
    wire signed [8:0] wa = $signed(g_lut(vea)) - $signed({4'd0, mea}) + $signed({4'd0, la1});
    wire signed [8:0] wb = $signed(g_lut(veb)) - $signed({4'd0, meb}) + $signed({4'd0, lb1});
    wire signed [8:0] rdiff = (wb - wa) >>> 1;
    wire [3:0]        ri = (rdiff < -9'sd8) ? 4'd0 : (rdiff > 9'sd7) ? 4'd15 : rdiff[3:0] + 4'd8;
    reg  [8:0]        t2_q;
    always @(posedge clk) t2_q <= t2_rom[{d10[9:3], ri}];

    // state updates with this sample
    wire signed [5:0] deva = $signed({1'b0, la1}) - $signed({1'b0, mea});
    wire signed [5:0] devb = $signed({1'b0, lb1}) - $signed({1'b0, meb});
    function automatic [7:0] sqc;    // min(dev^2, 255)
        input signed [5:0] dv;
        reg [5:0] m;
        begin
            m = dv[5] ? -dv : dv;
            sqc = (m > 6'd15) ? 8'd255 : m[3:0] * m[3:0];
        end
    endfunction
    wire [7:0]  sqa_c = sqc(deva), sqb_c = sqc(devb);
    wire [9:0]  dd = aa1 - ab1;
    reg  [9:0]  aa2;
    reg         en2;
    always @(posedge clk) begin
        if (rst) begin
            mea_acc <= 0; meb_acc <= 0; vea_acc <= 0; veb_acc <= 0; xr <= 0; xi <= 0;
        end else if (run1) begin
            vea_acc <= vea_acc + sqa_c - (vea_acc >> 6);
            veb_acc <= veb_acc + sqb_c - (veb_acc >> 6);
            mea_acc <= mea_acc + la1 - (mea_acc >> 6);
            meb_acc <= meb_acc + lb1 - (meb_acc >> 6);
            xr <= xr + cos6(dd[9:4]) - (xr >>> 7);
            xi <= xi + sin6(dd[9:4]) - (xi >>> 7);
        end
        aa2 <= aa1;
        en2 <= en_b;
    end

    // ---- C3 / C4: resultant angle, discriminator ------------------------------
    reg  [9:0] phi_r, phi_p;
    always @(posedge clk) begin
        phi_r <= en2 ? aa2 + {t2_q, 1'b0} : aa2;
        phi_p <= phi_r;
    end
    wire signed [9:0]  dp  = phi_r - phi_p;
    wire signed [19:0] acc = dp * 20'sd168 + 20'sd26317;
    always @(posedge clk)
        code <= acc[19] ? 6'd0 : (acc[19:10] > 10'd63) ? 6'd63 : acc[15:10];
endmodule
`default_nettype wire
