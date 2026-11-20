// C5VRX by Twotoz and the C5VRX contributors: three-branch ("Tripleversity")
// combiner and demodulator. Pre-detection maximal-ratio combining of C5 A
// with up to two more C5s (3-bit I/Q each: the top three bits of their 4-bit
// lane), a cross-product phase tracker per extra branch, then the CORDIC FM
// discriminator. The arithmetic and pipeline of mrc_core.v, generalised;
// bit-exact with fpga/model/mrc3_model.py run(..., disc=True, valid=...)
// (fpga/sim/run_tb3.py). raw_b / raw_c must be aligned to raw_a (align_b.v).
// va/vb/vc: the sample of that C5 is IQ (0 while it sends a marker): weight 0,
// its moments hold, a tracker holds while its branch or C5 A is invalid.
// Discriminator: the D32-R2 rule on the combined 16-bit angle (1024 sectors,
// 50 ns step, 0.7 of a step outside the video window), one code per 50 ns.
`default_nettype none

// tracker and rotation of one extra branch k against A (the mrc_core.v B path)
module mrc3_trk (
    input  wire              clk,
    input  wire              rst,
    input  wire              run2,
    input  wire              upd2,                         // S2: this sample updates the tracker
    input  wire [6:0]        idx2,
    input  wire signed [4:0] ca_i1, ca_q1, ck_i1, ck_q1,   // S1 cells
    output reg  signed [12:0] rk_i3, rk_q3                 // S3: c_k rotated by theta_k
);
    reg [15:0] cs_rom [0:255];
`include "rom_cs_init.vh"
    wire [17:0] p_ii, p_qq, p_qi, p_iq;
    mul9 u_ii (.a({{4{ca_i1[4]}}, ca_i1}), .b({{4{ck_i1[4]}}, ck_i1}), .y(p_ii));
    mul9 u_qq (.a({{4{ca_q1[4]}}, ca_q1}), .b({{4{ck_q1[4]}}, ck_q1}), .y(p_qq));
    mul9 u_qi (.a({{4{ca_q1[4]}}, ca_q1}), .b({{4{ck_i1[4]}}, ck_i1}), .y(p_qi));
    mul9 u_iq (.a({{4{ca_i1[4]}}, ca_i1}), .b({{4{ck_q1[4]}}, ck_q1}), .y(p_iq));
    reg signed [10:0] pr2, pi2;
    reg signed [4:0]  ck_i2, ck_q2;
    always @(posedge clk) begin
        pr2 <= $signed(p_ii[10:0]) + $signed(p_qq[10:0]);
        pi2 <= $signed(p_qi[10:0]) - $signed(p_iq[10:0]);
        ck_i2 <= ck_i1; ck_q2 <= ck_q1;
    end
    reg signed [19:0] xr, xi;
    wire signed [19:0] xr_n = xr + pr2 - (xr >>> 7), xi_n = xi + pi2 - (xi >>> 7);
    reg               th_go;
    reg signed [19:0] th_x, th_y;
    wire [15:0] th_ang;
    wire        th_done;
    cordic_iter #(.W(20), .N(10)) u_th (.clk(clk), .start(th_go), .x_in(th_x), .y_in(th_y), .ang(th_ang), .done(th_done));
    reg  [7:0]        th_pend;
    reg  signed [7:0] cos_pend, sin_pend, cos_r, sin_r;
    wire [17:0] r_ic, r_qs, r_is, r_qc;
    mul9 u_ic (.a({{4{ck_i2[4]}}, ck_i2}), .b({cos_r[7], cos_r}), .y(r_ic));
    mul9 u_qs (.a({{4{ck_q2[4]}}, ck_q2}), .b({sin_r[7], sin_r}), .y(r_qs));
    mul9 u_is (.a({{4{ck_i2[4]}}, ck_i2}), .b({sin_r[7], sin_r}), .y(r_is));
    mul9 u_qc (.a({{4{ck_q2[4]}}, ck_q2}), .b({cos_r[7], cos_r}), .y(r_qc));
    always @(posedge clk) begin
        th_go <= 1'b0;
        if (rst) begin
            xr <= 0; xi <= 0; cos_r <= 8'sd63; sin_r <= 8'sd0;
        end else if (run2) begin
            if (upd2) begin xr <= xr_n; xi <= xi_n; end
            if (idx2[3:0] == 4'd15) begin
                cos_r <= cos_pend; sin_r <= sin_pend;
                th_x <= (upd2 ? xr_n : xr) >>> 4; th_y <= (upd2 ? xi_n : xi) >>> 4; th_go <= 1'b1;
            end
        end
        rk_i3 <= $signed(r_ic[12:0]) - $signed(r_qs[12:0]);
        rk_q3 <= $signed(r_is[12:0]) + $signed(r_qc[12:0]);
    end
    reg [15:0] cs_q;
    always @(posedge clk) cs_q <= cs_rom[th_pend];
    reg th_lut, th_lut2;
    always @(posedge clk) begin
        if (rst) begin
            th_pend <= 8'd0; cos_pend <= 8'sd63; sin_pend <= 8'sd0; th_lut <= 1'b0; th_lut2 <= 1'b0;
        end else begin
            th_lut <= th_done; th_lut2 <= th_lut;
            if (th_done) th_pend <= (th_ang + 16'd128) >> 8;
            if (th_lut2) begin cos_pend <= cs_q[15:8]; sin_pend <= cs_q[7:0]; end
        end
    end
endmodule

module mrc3_core (
    input  wire       clk,
    input  wire       rst,
    input  wire [5:0] raw_a,        // {I2 I1 I0, Q2 Q1 Q0}, two's complement per component
    input  wire [5:0] raw_b,
    input  wire [5:0] raw_c,
    input  wire       en_b,
    input  wire       en_c,
    input  wire       va, vb, vc,   // per sample, with raw_*
    output reg  [5:0] code
);
    reg [7:0] g_rom   [0:255];
    reg [7:0] isq_rom [0:511];
`include "rom_gi_init.vh"

    function automatic [7:0] sq5;
        input signed [4:0] c;
        reg [3:0] m;
        begin m = c[4] ? -c : c; sq5 = m * m; end
    endfunction
    function automatic signed [4:0] cell3;   // 4 v + 2, -14..14
        input [2:0] v;
        begin cell3 = {v[2], v[2], v[2:0]} * 4 + 2; end
    endfunction

    // ---- R0 / S1 ----------------------------------------------------------------
    reg  [5:0] ra, rb, rc;
    reg  [2:0] v0, v1, v2, v3;
    reg  [6:0] idx0, idx1, idx2, idx3;
    reg        run0, run1, run2, run3;
    always @(posedge clk) begin
        if (rst) begin idx0 <= 7'd0; run0 <= 1'b0; end
        else begin
            ra <= raw_a; rb <= raw_b; rc <= raw_c; v0 <= {vc, vb, va};
            idx0 <= run0 ? idx0 + 7'd1 : 7'd0;
            run0 <= 1'b1;
        end
    end
    reg signed [4:0] ca_i1, ca_q1, cb_i1, cb_q1, cc_i1, cc_q1;
    always @(posedge clk) begin
        ca_i1 <= cell3(ra[5:3]); ca_q1 <= cell3(ra[2:0]);
        cb_i1 <= cell3(rb[5:3]); cb_q1 <= cell3(rb[2:0]);
        cc_i1 <= cell3(rc[5:3]); cc_q1 <= cell3(rc[2:0]);
        idx1 <= idx0; run1 <= run0 & ~rst; v1 <= v0;
    end

    // ---- S2: powers ------------------------------------------------------------
    reg [8:0] pa2, pb2, pc2;
    reg signed [4:0] ca_i2, ca_q2;
    always @(posedge clk) begin
        pa2 <= sq5(ca_i1) + sq5(ca_q1);
        pb2 <= sq5(cb_i1) + sq5(cb_q1);
        pc2 <= sq5(cc_i1) + sq5(cc_q1);
        ca_i2 <= ca_i1; ca_q2 <= ca_q1;
        idx2 <= idx1; run2 <= run1 & ~rst; v2 <= v1;
    end

    // ---- trackers (B, C) -----------------------------------------------------------
    wire signed [12:0] rb_i3, rb_q3, rc_i3, rc_q3;
    mrc3_trk u_tb (.clk(clk), .rst(rst), .run2(run2), .upd2(v2[0] & v2[1]), .idx2(idx2), .ca_i1(ca_i1), .ca_q1(ca_q1),
                   .ck_i1(cb_i1), .ck_q1(cb_q1), .rk_i3(rb_i3), .rk_q3(rb_q3));
    mrc3_trk u_tc (.clk(clk), .rst(rst), .run2(run2), .upd2(v2[0] & v2[2]), .idx2(idx2), .ca_i1(ca_i1), .ca_q1(ca_q1),
                   .ck_i1(cc_i1), .ck_q1(cc_q1), .rk_i3(rc_i3), .rk_q3(rc_q3));

    // ---- S3: moments ------------------------------------------------------------
    reg [15:0] m2 [0:2];
    reg [24:0] m4 [0:2];
    wire [8:0] pk [0:2];
    assign pk[0] = pa2; assign pk[1] = pb2; assign pk[2] = pc2;
    wire [17:0] psq [0:2];
    genvar gk;
    generate for (gk = 0; gk < 3; gk = gk + 1) begin : mom
        mul9 #(.A_SIGNED(0), .B_SIGNED(0)) u_sq (.a(pk[gk]), .b(pk[gk]), .y(psq[gk]));
    end endgenerate
    reg [15:0] snap_m2 [0:2];
    reg [24:0] snap_m4 [0:2];
    reg        snap_go;
    reg signed [4:0] ca_i3, ca_q3;
    integer k, kw, ku;
    always @(posedge clk) begin
        snap_go <= 1'b0;
        if (rst) begin
            for (k = 0; k < 3; k = k + 1) begin m2[k] <= 0; m4[k] <= 0; end
        end else if (run2) begin
            for (k = 0; k < 3; k = k + 1) if (v2[k]) begin
                m2[k] <= m2[k] + pk[k] - (m2[k] >> 6);
                m4[k] <= m4[k] + psq[k] - (m4[k] >> 6);
            end
            if (idx2 == 7'd127) begin
                for (k = 0; k < 3; k = k + 1) begin
                    snap_m2[k] <= v2[k] ? m2[k] + pk[k] - (m2[k] >> 6) : m2[k];
                    snap_m4[k] <= v2[k] ? m4[k] + psq[k] - (m4[k] >> 6) : m4[k];
                end
                snap_go <= 1'b1;
            end
        end
        ca_i3 <= ca_i2; ca_q3 <= ca_q2;
        idx3 <= idx2; run3 <= run2 & ~rst; v3 <= v2;
    end

    // ---- weight engine: three branches in turn ------------------------------------
    // 42 clocks a branch: the third weight is ready 127 clocks after the
    // snapshot, two before the hand-over at idx3 == 127 of the next period.
    reg  [7:0]  w_use [0:2];
    reg  [7:0]  pend  [0:2];
    reg         have_pend;
    reg  [3:0]  st;
    reg  [1:0]  wj;
    reg  [40:0] num, dsh;
    reg  [31:0] den;
    reg  [9:0]  q;
    reg  [4:0]  bitn;
    reg  [8:0]  mi;
    reg  [31:0] ma;
    reg  [15:0] mb;
    reg  [31:0] mp_acc;
    reg  [7:0]  g_q, isq_q, g_addr;
    always @(posedge clk) begin g_q <= g_rom[g_addr]; isq_q <= isq_rom[mi]; end
    localparam S_IDLE = 0, S_LOAD = 1, S_SQ = 2, S_SH = 3, S_CHK = 4, S_DIV = 5, S_LUT = 6,
               S_LUT2 = 7, S_GM = 8, S_WR = 9, S_NEXT = 10, S_STORE = 11;
    wire [15:0] m2j = snap_m2[wj];
    wire [24:0] m4j = snap_m4[wj];
    wire [9:0]  rq  = (q > 10'd511) ? 10'd511 : (q < 10'd256) ? 10'd256 : q;
    wire [7:0]  w_new = (mp_acc[15:8] == 8'd0) ? 8'd1 : mp_acc[15:8];
    always @(posedge clk) begin
        if (rst) begin
            st <= S_IDLE; have_pend <= 1'b0;
            for (kw = 0; kw < 3; kw = kw + 1) pend[kw] <= 8'd1;
        end else case (st)
            S_IDLE: if (snap_go) begin wj <= 2'd0; st <= S_LOAD; end
            S_LOAD: begin
                ma <= {16'd0, m2j}; mb <= m2j; mp_acc <= 32'd0;
                num <= {2'b0, m4j, 14'd0};
                mi <= (m2j[15:6] > 10'd511) ? 9'd511 : m2j[14:6];
                q <= 10'd0; bitn <= 5'd15; st <= S_SQ;
            end
            S_SQ: begin
                if (mb[0]) mp_acc <= mp_acc + ma;
                ma <= ma << 1; mb <= mb >> 1;
                if (bitn == 0) st <= S_SH; else bitn <= bitn - 5'd1;
            end
            S_SH: begin den <= mp_acc; dsh <= {mp_acc, 9'd0}; bitn <= 5'd9; st <= S_CHK; end
            S_CHK: begin
                if (den == 0) begin pend[wj] <= 8'd1; st <= S_NEXT; end
                else if (num >= {dsh[39:0], 1'b0}) begin q <= 10'd1023; st <= S_LUT; end
                else st <= S_DIV;
            end
            S_DIV: begin
                if (num >= dsh) begin num <= num - dsh; q <= q | (10'd1 << bitn); end
                dsh <= dsh >> 1;
                if (bitn == 0) st <= S_LUT; else bitn <= bitn - 5'd1;
            end
            S_LUT:  begin g_addr <= rq[7:0]; st <= S_LUT2; end
            S_LUT2: st <= S_GM;
            S_GM:   begin ma <= {24'd0, g_q}; mb <= {8'd0, isq_q}; mp_acc <= 32'd0; bitn <= 5'd7; st <= S_WR; end
            S_WR: begin
                if (mb[0]) mp_acc <= mp_acc + ma;
                ma <= ma << 1; mb <= mb >> 1;
                if (bitn == 0) st <= S_STORE; else bitn <= bitn - 5'd1;
            end
            S_STORE: begin pend[wj] <= w_new; st <= S_NEXT; end
            S_NEXT: begin
                if (wj != 2'd2) begin wj <= wj + 2'd1; st <= S_LOAD; end
                else begin have_pend <= 1'b1; st <= S_IDLE; end
            end
            default: st <= S_IDLE;
        endcase
    end

    // ---- S4: combine ------------------------------------------------------------------
    reg signed [23:0] zi4, zq4;
    wire [7:0] wa_eff = v3[0] ? w_use[0] : 8'd0;
    wire [7:0] wb_eff = en_b && v3[1] ? w_use[1] : 8'd0, wc_eff = en_c && v3[2] ? w_use[2] : 8'd0;
    wire [17:0] a_i, a_q;
    wire [35:0] b_i, b_q, c_i, c_q;
    mul9  u_ai (.a({1'b0, wa_eff}), .b({{4{ca_i3[4]}}, ca_i3}), .y(a_i));
    mul9  u_aq (.a({1'b0, wa_eff}), .b({{4{ca_q3[4]}}, ca_q3}), .y(a_q));
    mul18 u_bi (.a({10'd0, wb_eff}), .b({{5{rb_i3[12]}}, rb_i3}), .y(b_i));
    mul18 u_bq (.a({10'd0, wb_eff}), .b({{5{rb_q3[12]}}, rb_q3}), .y(b_q));
    mul18 u_ci (.a({10'd0, wc_eff}), .b({{5{rc_i3[12]}}, rc_i3}), .y(c_i));
    mul18 u_cq (.a({10'd0, wc_eff}), .b({{5{rc_q3[12]}}, rc_q3}), .y(c_q));
    always @(posedge clk) begin
        if (rst) begin
            for (ku = 0; ku < 3; ku = ku + 1) w_use[ku] <= 8'd1;
        end else if (run3 && idx3 == 7'd127 && have_pend) begin
            for (ku = 0; ku < 3; ku = ku + 1) w_use[ku] <= pend[ku];
        end
        zi4 <= $signed({a_i[17:0], 6'd0}) + $signed(b_i[23:0]) + $signed(c_i[23:0]);
        zq4 <= $signed({a_q[17:0], 6'd0}) + $signed(b_q[23:0]) + $signed(c_q[23:0]);
    end

    // ---- discriminator: D32-R2 rule on the combined angle (mrc3_model.disc50) ----
    wire [15:0] phi;
    cordic_angle #(.W(18), .N(10)) u_phi (.clk(clk), .x_in(zi4[23:6]), .y_in(zq4[23:6]), .ang(phi));
    // sample parity and run, carried along zi4 (1) and the CORDIC (N + 1 = 11)
    reg [11:0] ev_d, run_d;
    always @(posedge clk) begin
        ev_d  <= {ev_d[10:0], ~idx3[0]};
        run_d <= rst ? 12'd0 : {run_d[10:0], run3};
    end
    wire        ev_phi = ev_d[11] & run_d[11];
    wire [9:0]  tok = phi[15:6] ^ 10'h200;          // ((phi + 32768) >> 6) & 1023
    reg  [9:0]  st_s;
    reg         st_ok;
    wire signed [9:0]  d = tok - st_s;               // wrapped to -512..511
    wire signed [17:0] d179 = d * 18'sd179;
    wire        inwin = d >= -10'sd312 && d <= 10'sd384;
    wire signed [9:0]  step = inwin ? d : d179[17:8];
    wire signed [17:0] cx = step * 18'sd105 + 18'sd32896;
    wire signed [9:0]  cy = cx[17:8];                // floor(cx / 256)
    wire [17:0] c5 = cy[8:0] * 9'd205;               // floor(cy / 5) for 0 <= cy < 1029
    wire [7:0]  cq = c5[17:10];
    always @(posedge clk) begin
        if (rst) begin st_ok <= 1'b0; code <= 6'd25; end
        else if (ev_phi) begin
            if (!st_ok) begin st_s <= tok; st_ok <= 1'b1; code <= 6'd25; end
            else begin
                st_s <= st_s + step;
                code <= cy[9] ? 6'd0 : (cq > 8'd63) ? 6'd63 : cq[5:0];
            end
        end
    end
endmodule
`default_nettype wire
