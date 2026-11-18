// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL combiner and
// demodulator. Pre-detection maximal-ratio combining of two C5 IQ streams
// with a cross-product phase tracker, then a CORDIC FM discriminator, one
// six-bit code per 40 MS/s sample. Bit-exact with fpga/model/mrc_model.py
// (fpga/sim/run_tb.py checks it); the model's docstring holds the
// arithmetic. One sample per clock (C5 A's RX40 clock); raw_b must already
// be aligned to raw_a (align_b.v). Latency raw -> code: 17 clocks.
// Extends the dual-C5 host study (fpga/model/eval_model.py) and the
// C5VRX-5 decoder output window.
`default_nettype none
module mrc_core (
    input  wire       clk,
    input  wire       rst,
    input  wire [7:0] raw_a,        // {I[3:0], Q[3:0]}, sign bit 3
    input  wire [7:0] raw_b,
    input  wire       en_b,         // 0: C5 A alone (B absent or not aligned)
    output reg  [5:0] code,
    output wire [7:0] w_a,          // weights in use (status)
    output wire [7:0] w_b,
    output wire [7:0] theta         // tracker angle in use, 2^-8 turns (status)
);
    reg [7:0]  g_rom   [0:255];
    reg [7:0]  isq_rom [0:511];
    reg [15:0] cs_rom  [0:255];      // {cos, sin}, Q1.6
`include "rom_init.vh"

    // square of an odd cell value (-15..15), as a table in logic
    function automatic [7:0] sq5;
        input signed [4:0] c;
        reg [3:0] m;
        begin
            m = c[4] ? -c : c;
            sq5 = m * m;
        end
    endfunction

    // ---- R0 / S1: decode ----------------------------------------------------
    reg  [7:0] ra, rb;
    reg  [6:0] idx0, idx1, idx2;
    reg        run0, run1, run2;
    always @(posedge clk) begin
        if (rst) begin
            idx0 <= 7'd0;
            run0 <= 1'b0;
        end else begin
            ra   <= raw_a;
            rb   <= raw_b;
            idx0 <= run0 ? idx0 + 7'd1 : 7'd0;
            run0 <= 1'b1;
        end
    end

    function automatic signed [4:0] cellv;   // 2 v + 1, odd -15..15
        input [3:0] nib;
        begin
            cellv = {nib[3], nib[3:0]} * 2 + 1;
        end
    endfunction

    reg signed [4:0] ca_i1, ca_q1, cb_i1, cb_q1;
    always @(posedge clk) begin
        ca_i1 <= cellv(ra[7:4]);
        ca_q1 <= cellv(ra[3:0]);
        cb_i1 <= cellv(rb[7:4]);
        cb_q1 <= cellv(rb[3:0]);
        idx1  <= idx0;
        run1  <= run0 & ~rst;
    end

    // ---- S2: powers and cross product --------------------------------------
    reg        [8:0]  pa2, pb2;
    reg signed [10:0] pr2, pi2;
    reg signed [4:0]  ca_i2, ca_q2, cb_i2, cb_q2;
    wire [17:0] p_ii, p_qq, p_qi, p_iq;
    mul9 u_ii (.a({{4{ca_i1[4]}}, ca_i1}), .b({{4{cb_i1[4]}}, cb_i1}), .y(p_ii));
    mul9 u_qq (.a({{4{ca_q1[4]}}, ca_q1}), .b({{4{cb_q1[4]}}, cb_q1}), .y(p_qq));
    mul9 u_qi (.a({{4{ca_q1[4]}}, ca_q1}), .b({{4{cb_i1[4]}}, cb_i1}), .y(p_qi));
    mul9 u_iq (.a({{4{ca_i1[4]}}, ca_i1}), .b({{4{cb_q1[4]}}, cb_q1}), .y(p_iq));
    always @(posedge clk) begin
        pa2   <= sq5(ca_i1) + sq5(ca_q1);
        pb2   <= sq5(cb_i1) + sq5(cb_q1);
        pr2   <= $signed(p_ii[10:0]) + $signed(p_qq[10:0]);
        pi2   <= $signed(p_qi[10:0]) - $signed(p_iq[10:0]);
        ca_i2 <= ca_i1; ca_q2 <= ca_q1; cb_i2 <= cb_i1; cb_q2 <= cb_q1;
        idx2  <= idx1;
        run2  <= run1 & ~rst;
    end

    // ---- S3: moments, tracker, rotation -------------------------------------
    reg        [15:0] m2a, m2b;
    reg        [24:0] m4a, m4b;
    reg signed [19:0] xr, xi;
    wire       [15:0] m2a_n = m2a + pa2 - (m2a >> 6);
    wire       [15:0] m2b_n = m2b + pb2 - (m2b >> 6);
    wire [17:0] pa_sq, pb_sq;
    mul9 #(.A_SIGNED(0), .B_SIGNED(0)) u_pa (.a(pa2), .b(pa2), .y(pa_sq));
    mul9 #(.A_SIGNED(0), .B_SIGNED(0)) u_pb (.a(pb2), .b(pb2), .y(pb_sq));
    wire       [24:0] m4a_n = m4a + pa_sq - (m4a >> 6);
    wire       [24:0] m4b_n = m4b + pb_sq - (m4b >> 6);
    wire signed [19:0] xr_n = xr + pr2 - (xr >>> 7);
    wire signed [19:0] xi_n = xi + pi2 - (xi >>> 7);

    reg  [15:0] snap_m2a, snap_m2b;
    reg  [24:0] snap_m4a, snap_m4b;
    reg         snap_go, th_go;
    reg  signed [19:0] th_x, th_y;

    // tracker angle engine: iterative CORDIC of the accumulator (>> 4)
    wire [15:0] th_ang;
    wire        th_done;
    cordic_iter #(.W(20), .N(10)) u_th (.clk(clk), .start(th_go), .x_in(th_x), .y_in(th_y),
                                         .ang(th_ang), .done(th_done));
    reg  [7:0]        th_pend, th_cur;
    reg               th_lut;
    reg  signed [7:0] cos_pend, sin_pend, cos_r, sin_r;

    reg signed [12:0] rb_i3, rb_q3;
    wire [17:0] r_ic, r_qs, r_is, r_qc;
    mul9 u_ic (.a({{4{cb_i2[4]}}, cb_i2}), .b({cos_r[7], cos_r}), .y(r_ic));
    mul9 u_qs (.a({{4{cb_q2[4]}}, cb_q2}), .b({sin_r[7], sin_r}), .y(r_qs));
    mul9 u_is (.a({{4{cb_i2[4]}}, cb_i2}), .b({sin_r[7], sin_r}), .y(r_is));
    mul9 u_qc (.a({{4{cb_q2[4]}}, cb_q2}), .b({cos_r[7], cos_r}), .y(r_qc));
    reg signed [4:0]  ca_i3, ca_q3;
    reg  [6:0]        idx3;
    reg               run3;

    always @(posedge clk) begin
        snap_go <= 1'b0;
        th_go   <= 1'b0;
        if (rst) begin
            m2a <= 0; m2b <= 0; m4a <= 0; m4b <= 0; xr <= 0; xi <= 0;
            th_cur <= 8'd0; cos_r <= 8'sd63; sin_r <= 8'sd0;
        end else if (run2) begin
            m2a <= m2a_n; m2b <= m2b_n; m4a <= m4a_n; m4b <= m4b_n;
            xr  <= xr_n;
            xi  <= xi_n;
            if (idx2[3:0] == 4'd15) begin          // end of a 16-sample block
                th_cur <= th_pend; cos_r <= cos_pend; sin_r <= sin_pend;
                th_x   <= xr_n >>> 4;
                th_y   <= xi_n >>> 4;
                th_go  <= 1'b1;
            end
            if (idx2 == 7'd127) begin
                snap_m2a <= m2a_n; snap_m2b <= m2b_n;
                snap_m4a <= m4a_n; snap_m4b <= m4b_n;
                snap_go  <= 1'b1;
            end
        end
        // rotation of B by the tracker angle in use (registered cos/sin)
        rb_i3 <= $signed(r_ic[12:0]) - $signed(r_qs[12:0]);
        rb_q3 <= $signed(r_is[12:0]) + $signed(r_qc[12:0]);
        ca_i3 <= ca_i2; ca_q3 <= ca_q2;
        idx3  <= idx2;
        run3  <= run2 & ~rst;
    end

    // new tracker angle -> table (block RAM, registered read)
    reg [15:0] cs_q;
    always @(posedge clk) cs_q <= cs_rom[th_pend];
    reg th_lut2;
    always @(posedge clk) begin
        if (rst) begin
            th_pend <= 8'd0; cos_pend <= 8'sd63; sin_pend <= 8'sd0; th_lut <= 1'b0; th_lut2 <= 1'b0;
        end else begin
            th_lut  <= th_done;
            th_lut2 <= th_lut;
            if (th_done) th_pend <= (th_ang + 16'd128) >> 8;
            if (th_lut2) begin
                cos_pend <= cs_q[15:8];
                sin_pend <= cs_q[7:0];
            end
        end
    end
    assign theta = th_cur;

    // ---- weight engine (sequential, between snapshot and hand-over) ---------
    // m2^2 and G*ISQ use one shift-add multiplier; the division is restoring.
    reg  [7:0]  w_use_a, w_use_b, pend_a, pend_b;
    reg         have_pend;
    reg  [3:0]  st;
    reg         wj;              // branch being computed
    reg  [40:0] num, dsh;        // dividend, divisor << bit
    reg  [31:0] den;
    reg  [9:0]  q;
    reg  [4:0]  bitn;
    reg  [8:0]  mi;
    reg  [31:0] ma;              // multiplicand (shifted left)
    reg  [15:0] mb;              // multiplier (shifted right)
    reg  [31:0] mp_acc;
    reg  [7:0]  g_q, isq_q;
    reg  [7:0]  g_addr;
    always @(posedge clk) begin  // block RAM reads
        g_q   <= g_rom[g_addr];
        isq_q <= isq_rom[mi];
    end
    localparam S_IDLE = 0, S_LOAD = 1, S_SQ = 2, S_SH = 3, S_CHK = 4, S_DIV = 5, S_LUT = 6,
               S_LUT2 = 7, S_GM = 8, S_WR = 9, S_NEXT = 10;
    wire [15:0] m2j = wj ? snap_m2b : snap_m2a;
    wire [24:0] m4j = wj ? snap_m4b : snap_m4a;
    wire [9:0]  rq  = (q > 10'd511) ? 10'd511 : (q < 10'd256) ? 10'd256 : q;
    wire [7:0]  w_new = (mp_acc[15:8] == 8'd0) ? 8'd1 : mp_acc[15:8];
    always @(posedge clk) begin
        if (rst) begin
            st <= S_IDLE; have_pend <= 1'b0; pend_a <= 8'd1; pend_b <= 8'd1;
        end else case (st)
            S_IDLE: if (snap_go) begin wj <= 1'b0; st <= S_LOAD; end
            S_LOAD: begin
                ma     <= {16'd0, m2j};
                mb     <= m2j;
                mp_acc <= 32'd0;
                num    <= {2'b0, m4j, 14'd0};
                mi     <= (m2j[15:6] > 10'd511) ? 9'd511 : m2j[14:6];
                q      <= 10'd0;
                bitn   <= 5'd15;
                st     <= S_SQ;
            end
            S_SQ: begin                    // den = m2^2, 16 steps
                if (mb[0]) mp_acc <= mp_acc + ma;
                ma <= ma << 1;
                mb <= mb >> 1;
                if (bitn == 0) st <= S_SH; else bitn <= bitn - 5'd1;
            end
            S_SH: begin
                den  <= mp_acc;
                dsh  <= {mp_acc, 9'd0};    // den << 9; den << 10 is checked first
                bitn <= 5'd9;
                st   <= S_CHK;
            end
            S_CHK: begin
                if (den == 0) begin
                    if (wj) pend_b <= 8'd1; else pend_a <= 8'd1;
                    st <= S_NEXT;
                end else if (num >= {dsh[39:0], 1'b0}) begin
                    q  <= 10'd1023;        // quotient >= 1024: clamps to 511
                    st <= S_LUT;
                end else st <= S_DIV;
            end
            S_DIV: begin                   // restoring division, quotient bits 9..0
                if (num >= dsh) begin
                    num <= num - dsh;
                    q   <= q | (10'd1 << bitn);
                end
                dsh <= dsh >> 1;
                if (bitn == 0) st <= S_LUT; else bitn <= bitn - 5'd1;
            end
            S_LUT: begin
                g_addr <= rq[7:0];
                st     <= S_LUT2;
            end
            S_LUT2: st <= S_GM;            // ROM read latency
            S_GM: begin
                ma     <= {24'd0, g_q};
                mb     <= {8'd0, isq_q};
                mp_acc <= 32'd0;
                bitn   <= 5'd7;
                st     <= S_WR;
            end
            S_WR: begin                    // G * ISQ, 8 steps, then store
                if (mb[0]) mp_acc <= mp_acc + ma;
                ma <= ma << 1;
                mb <= mb >> 1;
                if (bitn == 0) st <= S_NEXT + 1; else bitn <= bitn - 5'd1;
            end
            S_NEXT + 1: begin
                if (wj) pend_b <= w_new; else pend_a <= w_new;
                st <= S_NEXT;
            end
            S_NEXT: begin
                if (!wj) begin wj <= 1'b1; st <= S_LOAD; end
                else begin have_pend <= 1'b1; st <= S_IDLE; end
            end
            default: st <= S_IDLE;
        endcase
    end

    // ---- S4: combine ----------------------------------------------------------
    reg signed [23:0] zi4, zq4;
    wire       [7:0]  wb_eff = en_b ? w_use_b : 8'd0;
    wire [17:0] a_i, a_q;
    wire [35:0] b_i, b_q;
    mul9  u_ai (.a({1'b0, w_use_a}), .b({{4{ca_i3[4]}}, ca_i3}), .y(a_i));
    mul9  u_aq (.a({1'b0, w_use_a}), .b({{4{ca_q3[4]}}, ca_q3}), .y(a_q));
    mul18 u_bi (.a({10'd0, wb_eff}), .b({{5{rb_i3[12]}}, rb_i3}), .y(b_i));
    mul18 u_bq (.a({10'd0, wb_eff}), .b({{5{rb_q3[12]}}, rb_q3}), .y(b_q));
    always @(posedge clk) begin
        if (rst) begin
            w_use_a <= 8'd1; w_use_b <= 8'd1;
        end else if (run3 && idx3 == 7'd127 && have_pend) begin
            w_use_a <= pend_a; w_use_b <= pend_b;
        end
        zi4 <= $signed({a_i[17:0], 6'd0}) + $signed(b_i[23:0]);
        zq4 <= $signed({a_q[17:0], 6'd0}) + $signed(b_q[23:0]);
    end
    assign w_a = w_use_a;
    assign w_b = wb_eff;

    // ---- discriminator ---------------------------------------------------------
    wire [15:0] phi;
    cordic_angle #(.W(18), .N(10)) u_phi (.clk(clk), .x_in(zi4[23:6]), .y_in(zq4[23:6]), .ang(phi));
    reg  [15:0] phi_prev;
    reg  signed [31:0] acc;
    always @(posedge clk) begin
        phi_prev <= phi;
        acc      <= $signed(phi - phi_prev) * 32'sd168 + 32'sd1684275;
        code     <= (acc[31]) ? 6'd0 : (acc[31:16] > 16'd63) ? 6'd63 : acc[21:16];
    end
endmodule
`default_nettype wire
