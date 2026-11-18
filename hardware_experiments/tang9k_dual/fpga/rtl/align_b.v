// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL C5 B alignment.
//
// C5 B runs on its own crystal: its RX40 clock and sample stream are
// captured in clk_b, written into a 64-entry buffer and read in clk_a (C5 A,
// the timing master) at a read pointer that this module steers so that the
// B sample read lines up with the current A sample. The host study showed
// the combiner tolerates up to 25 ns (one sample), so whole samples suffice:
// no interpolator.
//
// The alignment metric is the magnitude of the low-passed cross product
// A * conj(B) (the FM modulation cancels only when both streams are aligned;
// the same quantity the combiner's phase tracker uses). Three lags
// (pointer -1, 0, +1) are correlated in parallel.
//   ACQUIRE: every pointer offset 4..60 behind the write pointer is held for
//            2048 samples; the best one wins.
//   TRACK:   over every 2048-sample window the magnitudes of lags -1, 0
//            and +1 are summed; the pointer steps to a neighbour whose sum
//            beats the centre. For a parabolic peak that is exactly "the
//            true offset is more than half a sample away" (the peak of an
//            FM carrier is flat, so any extra margin biases the error).
//            Clock drift of a few tens of ppm is one slip every ~1 ms.
// en_b (B joins the combiner) needs coherence over the decision window:
// sum |x| > sum (|A|^2 + |B|^2) / 16, a normalised correlation above about
// 1/8 (about -8 dB C/N per branch for aligned streams of equal power);
// else the combiner runs on A alone. A stopped B clock, a lost
// coherence for 16 windows or a pointer at the buffer edge restarts
// ACQUIRE. Extends the dual-C5 study in fpga/model/eval_model.py.
`default_nettype none
module align_b (
    input  wire       clk_b,
    input  wire [7:0] raw_b_in,     // captured in clk_b
    input  wire       clk_a,
    input  wire       rst_a,
    input  wire [7:0] raw_a,        // current A sample (clk_a)
    output reg  [7:0] raw_a_d,        // raw_a, one clock later (pairs with raw_b)
    output reg  [7:0] raw_b,        // B sample aligned to raw_a_d (clk_a)
    output reg        en_b,
    output reg        b_alive,
    output reg  [5:0] lag           // pointer offset behind the write pointer (status)
);
    // ---- write side (clk_b) ---------------------------------------------------
    reg  [7:0] mem [0:63];
    integer    mi;
    initial for (mi = 0; mi < 64; mi = mi + 1) mem[mi] = 8'd0;
    reg  [5:0] wp = 6'd0;
    reg  [5:0] wp_gray = 6'd0;
    always @(posedge clk_b) begin
        mem[wp] <= raw_b_in;
        wp      <= wp + 6'd1;
        wp_gray <= (wp + 6'd1) ^ ((wp + 6'd1) >> 1);
    end

    // ---- pointer into clk_a ----------------------------------------------------
    reg [5:0] g1 = 6'd0, g2 = 6'd0, wps = 6'd0;
    function automatic [5:0] gray2bin;
        input [5:0] g;
        integer k;
        begin
            gray2bin[5] = g[5];
            for (k = 4; k >= 0; k = k - 1) gray2bin[k] = gray2bin[k + 1] ^ g[k];
        end
    endfunction
    wire [5:0] bin = gray2bin(g2);
    reg [5:0] wps_prev;
    reg [6:0] still;
    always @(posedge clk_a) begin
        g1 <= wp_gray;
        g2 <= g1;
        wps <= bin;
        wps_prev <= wps;
        if (rst_a) begin
            still <= 7'd0; b_alive <= 1'b0;
        end else if (wps == wps_prev) begin
            if (still != 7'd127) still <= still + 7'd1;
            if (still >= 7'd63) b_alive <= 1'b0;
        end else begin
            still <= 7'd0; b_alive <= 1'b1;
        end
    end

    // ---- read side ---------------------------------------------------------------
    reg  [5:0] rp = 6'd0;
    reg  [7:0] bm = 8'd0, b0 = 8'd0, bp = 8'd0;   // lags -1, 0, +1 (pointer rp-1, rp, rp+1)
    always @(posedge clk_a) begin
        bm    <= mem[rp - 6'd1];
        b0    <= mem[rp];
        bp    <= mem[rp + 6'd1];
        raw_b <= mem[rp];
    end
    reg [7:0] a1;
    always @(posedge clk_a) a1 <= raw_a;     // pairs with bm/b0/bp (one register)
    always @(*) raw_a_d = a1;

    function automatic signed [4:0] cv;      // 2 v + 1
        input [3:0] nib;
        cv = {nib[3], nib} * 2 + 1;
    endfunction

    // cross products of A with the three lags, one-pole low-pass (tau 128)
    reg signed [19:0] xr [0:2];
    reg signed [19:0] xi [0:2];
    wire signed [4:0] ai = cv(a1[7:4]), aq = cv(a1[3:0]);
    wire [7:0] bsel [0:2];
    assign bsel[0] = bm; assign bsel[1] = b0; assign bsel[2] = bp;
    genvar l;
    generate
        for (l = 0; l < 3; l = l + 1) begin : corr
            wire signed [4:0] bi_ = cv(bsel[l][7:4]), bq_ = cv(bsel[l][3:0]);
            wire [17:0] p_ii, p_qq, p_qi, p_iq;
            mul9 u_ii (.a({{4{ai[4]}}, ai}), .b({{4{bi_[4]}}, bi_}), .y(p_ii));
            mul9 u_qq (.a({{4{aq[4]}}, aq}), .b({{4{bq_[4]}}, bq_}), .y(p_qq));
            mul9 u_qi (.a({{4{aq[4]}}, aq}), .b({{4{bi_[4]}}, bi_}), .y(p_qi));
            mul9 u_iq (.a({{4{ai[4]}}, ai}), .b({{4{bq_[4]}}, bq_}), .y(p_iq));
            wire signed [10:0] pr = $signed(p_ii[10:0]) + $signed(p_qq[10:0]);
            wire signed [10:0] pq = $signed(p_qi[10:0]) - $signed(p_iq[10:0]);
            always @(posedge clk_a) begin
                if (rst_a || !b_alive) begin xr[l] <= 0; xi[l] <= 0; end
                else begin
                    xr[l] <= xr[l] + pr - (xr[l] >>> 7);
                    xi[l] <= xi[l] + pq - (xi[l] >>> 7);
                end
            end
        end
    endgenerate

    // magnitude ~ max + min/2 of |re|, |im|
    function automatic [19:0] mag;
        input signed [19:0] r, i;
        reg [19:0] ar, ai2;
        begin
            ar  = r[19] ? -r : r;
            ai2 = i[19] ? -i : i;
            mag = (ar > ai2) ? ar + (ai2 >> 1) : ai2 + (ar >> 1);
        end
    endfunction
    wire [19:0] mm = mag(xr[0], xi[0]), m0 = mag(xr[1], xi[1]), mp = mag(xr[2], xi[2]);

    // envelope powers for the coherence test (same tau)
    reg [15:0] pa_acc, pb_acc;
    function automatic [7:0] sq5;
        input signed [4:0] c;
        reg [3:0] m;
        begin
            m = c[4] ? -c : c;
            sq5 = m * m;
        end
    endfunction
    wire [8:0] pa = sq5(ai) + sq5(aq);
    wire signed [4:0] b0i = cv(b0[7:4]), b0q = cv(b0[3:0]);
    wire [8:0] pb = sq5(b0i) + sq5(b0q);
    always @(posedge clk_a) begin
        if (rst_a) begin pa_acc <= 0; pb_acc <= 0; end
        else begin
            pa_acc <= pa_acc + pa - (pa_acc >> 7);
            pb_acc <= pb_acc + pb - (pb_acc >> 7);
        end
    end

    // window peaks: sampled at the end of each 2048-sample window
    localparam ACQ = 1'b0, TRK = 1'b1;
    reg        state;
    reg [10:0] win;
    reg [5:0]  off, best_off;        // offset behind wps
    reg [19:0] best_mag;
    reg [3:0]  lost;
    reg  [31:0] sm, s0, sp;          // window sums of mm, m0, mp
    wire [31:0] sm_n = sm + mm, s0_n = s0 + m0, sp_n = sp + mp;
    wire [31:0] s0_thr = s0_n;       // parabolic-peak rule: a neighbour above the centre means |error| > 0.5
    wire [5:0] fill = wps - rp;      // how far rp is behind the write pointer
    // coherence over the window: sum m0 > sum (pa + pb) / 16, i.e. a
    // normalised correlation above ~1/8 for equal branch powers (all ~128x
    // scaled alike; no multiplier)
    reg  [31:0] spp;
    wire [31:0] spp_n = spp + pa_acc + pb_acc;
    wire coherent = s0_n > (spp_n >> 4);

    always @(posedge clk_a) begin
        if (rst_a || !b_alive) begin
            state <= ACQ; win <= 11'd0; off <= 6'd4; best_off <= 6'd32; best_mag <= 20'd0;
            sm <= 0; s0 <= 0; sp <= 0; spp <= 0;
            en_b <= 1'b0; lost <= 4'd0; rp <= wps - 6'd4;
        end else begin
            win <= win + 11'd1;
            if (state == ACQ) begin
                rp <= wps - off;         // hold this offset for the window
                if (win == 11'd2047) begin
                    if (m0 > best_mag) begin best_mag <= m0; best_off <= off; end
                    if (off == 6'd60) begin
                        state <= TRK;
                        rp    <= wps - ((m0 > best_mag) ? off : best_off);
                    end else off <= off + 6'd1;
                end
            end else begin
                rp   <= rp + 6'd1;       // one sample per clock
                sm <= sm_n; s0 <= s0_n; sp <= sp_n; spp <= spp_n;
                if (win == 11'd2047) begin
                    sm <= 0; s0 <= 0; sp <= 0; spp <= 0;
                    if (sp_n > s0_thr && sp_n >= sm_n) rp <= rp + 6'd2;   // newer B sample fits better
                    else if (sm_n > s0_thr)            rp <= rp;          // older B sample fits better
                    if (coherent) begin
                        en_b <= 1'b1; lost <= 4'd0;
                    end else begin
                        en_b <= 1'b0;
                        lost <= lost + 4'd1;
                        if (lost == 4'd15) begin
                            state <= ACQ; off <= 6'd4; best_mag <= 20'd0;
                        end
                    end
                    if (fill < 6'd3 || fill > 6'd61) begin   // buffer edge: start over
                        state <= ACQ; off <= 6'd4; best_mag <= 20'd0; en_b <= 1'b0;
                    end
                end
            end
        end
        lag <= fill;
    end
endmodule
`default_nettype wire
