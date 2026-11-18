// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL C5 B alignment,
// lite version for the Tang Nano 1K. Same job and state machine as
// align_b.v (64-entry dual-clock buffer in block RAM, ACQUIRE over offsets
// 4..60, TRACK by lags -1/0/+1), with sign-only correlators: the quadrant
// phasors of A and B (I and Q sign bits) are multiplied with xor logic,
// summed over 128-sample sub-windows (the LO difference between the C5s
// turns slowly within 3.2 us) and their magnitudes summed over 16
// sub-windows (2048 samples). The correlation of hard-limited carriers still
// peaks at alignment. en_b needs the centre sum above COH (~2.4x the
// noise-only mean, a normalised correlation of about 0.2, roughly -4 dB C/N
// per branch). Extends align_b.v.
`default_nettype none
module align_lite #(
    parameter COH = 16'd700
) (
    input  wire       clk_b,
    input  wire [7:0] raw_b_in,
    input  wire       clk_a,
    input  wire       rst_a,
    input  wire [7:0] raw_a,
    output wire [7:0] raw_a_d,      // raw_a two clocks later (pairs with raw_b)
    output wire [7:0] raw_b,        // aligned B sample
    output reg        en_b,
    output reg        b_alive
);
    (* syn_ramstyle = "block_ram" *) reg [7:0] mem [0:63];
    reg  [5:0] wp = 6'd0, wp_gray = 6'd0;
    always @(posedge clk_b) begin
        mem[wp] <= raw_b_in;
        wp      <= wp + 6'd1;
        wp_gray <= (wp + 6'd1) ^ ((wp + 6'd1) >> 1);
    end

    function automatic [5:0] gray2bin;
        input [5:0] g;
        integer k;
        begin
            gray2bin[5] = g[5];
            for (k = 4; k >= 0; k = k - 1) gray2bin[k] = gray2bin[k + 1] ^ g[k];
        end
    endfunction
    reg [5:0] g1 = 6'd0, g2 = 6'd0, wps = 6'd0, wps_prev = 6'd0;
    reg [6:0] still = 7'd0;
    always @(posedge clk_a) begin
        g1 <= wp_gray; g2 <= g1; wps <= gray2bin(g2); wps_prev <= wps;
        if (rst_a) begin still <= 0; b_alive <= 1'b0; end
        else if (wps == wps_prev) begin
            if (still != 7'd127) still <= still + 7'd1;
            if (still >= 7'd63) b_alive <= 1'b0;
        end else begin still <= 0; b_alive <= 1'b1; end
    end

    // read: block RAM, one sample per clock; the two previous reads give the lags
    reg  [5:0] rp = 6'd0;
    reg  [7:0] bq = 8'd0, bq1 = 8'd0, bq2 = 8'd0, a1 = 8'd0, a2 = 8'd0;
    always @(posedge clk_a) begin
        bq <= mem[rp]; bq1 <= bq; bq2 <= bq1;
        a1 <= raw_a;   a2 <= a1;
    end
    assign raw_a_d = a2;
    assign raw_b   = bq1;

    // sign correlators: lag 0 = bq2 (older), 1 = bq1 (centre), 2 = bq (newer)
    function automatic [3:0] sprod;   // {re[1:0], im[1:0]} as -1/0/+1 halves, two's complement
        input ai, aq, bi, bq_;
        reg signed [2:0] re, im;
        begin
            re = ((ai == bi) ? 3'sd1 : -3'sd1) + ((aq == bq_) ? 3'sd1 : -3'sd1);
            im = ((aq == bi) ? 3'sd1 : -3'sd1) - ((ai == bq_) ? 3'sd1 : -3'sd1);
            sprod = {re[2:1], im[2:1]};    // re, im are even: halve
        end
    endfunction
    wire [7:0] bl [0:2];
    assign bl[0] = bq2; assign bl[1] = bq1; assign bl[2] = bq;
    reg signed [9:0] cr [0:2];
    reg signed [9:0] ci [0:2];
    reg        [6:0] sub = 7'd0;
    reg        [3:0] nsub = 4'd0;
    reg        [14:0] ms [0:2];       // magnitude sums of the current window
    reg        [14:0] mw [0:2];       // of the last complete window
    reg signed [9:0]  hr [0:2];       // sub-window sums waiting for the magnitude unit
    reg signed [9:0]  hi [0:2];
    genvar l;
    generate
        for (l = 0; l < 3; l = l + 1) begin : lag
            wire [3:0] p = sprod(a2[7], a2[3], bl[l][7], bl[l][3]);
            always @(posedge clk_a) begin
                if (sub == 7'd127) begin
                    hr[l] <= cr[l] + $signed(p[3:2]);
                    hi[l] <= ci[l] + $signed(p[1:0]);
                    cr[l] <= 10'sd0;
                    ci[l] <= 10'sd0;
                end else begin
                    cr[l] <= cr[l] + $signed(p[3:2]);
                    ci[l] <= ci[l] + $signed(p[1:0]);
                end
            end
        end
    endgenerate

    // one magnitude unit, shared: lag 0, 1, 2 in the first three clocks of a sub-window
    wire [1:0] ml = sub[1:0];
    wire signed [9:0] mr = hr[ml], mi_ = hi[ml];
    wire [9:0] ar = mr[9] ? -mr : mr, ai_ = mi_[9] ? -mi_ : mi_;
    wire [9:0] mg = (ar > ai_) ? ar + (ai_ >> 1) : ai_ + (ar >> 1);
    reg        dec = 1'b0;            // one clock after a window's last magnitude
    always @(posedge clk_a) begin
        dec <= 1'b0;
        if (sub < 7'd3 && sub[6:2] == 5'd0) begin
            if (nsub == 4'd0) begin                 // first sub-window of a new window:
                mw[ml] <= ms[ml] + mg;              // the previous window is complete
                ms[ml] <= 15'd0;
                if (ml == 2'd2) dec <= 1'b1;
            end else ms[ml] <= ms[ml] + mg;
        end
    end

    // acquisition and tracking, one decision per 16 sub-windows
    localparam ACQ = 1'b0, TRK = 1'b1;
    reg        state = ACQ;
    reg [5:0]  off = 6'd4, best_off = 6'd32;
    reg [14:0] best = 15'd0;
    reg [3:0]  lost = 4'd0;
    wire [14:0] mm = mw[0], m0 = mw[1], mp = mw[2];
    wire [5:0] fill = wps - rp;
    always @(posedge clk_a) begin
        sub <= sub + 7'd1;
        if (sub == 7'd127) nsub <= nsub + 4'd1;
        if (rst_a || !b_alive) begin
            state <= ACQ; off <= 6'd4; best <= 0; best_off <= 6'd32; en_b <= 1'b0; lost <= 0;
            rp <= wps - 6'd4;
        end else if (state == ACQ) begin
            rp <= wps - off;
            if (dec) begin
                if (m0 > best) begin best <= m0; best_off <= off; end
                if (off == 6'd60) begin
                    state <= TRK;
                    rp    <= wps - ((m0 > best) ? off : best_off);
                end else off <= off + 6'd1;
            end
        end else begin
            rp <= rp + 6'd1;
            if (dec) begin
                if (mp > m0 && mp >= mm) rp <= rp + 6'd2;    // newer B fits better
                else if (mm > m0)        rp <= rp;           // older B fits better
                if (m0 > COH) begin en_b <= 1'b1; lost <= 0; end
                else begin
                    en_b <= 1'b0; lost <= lost + 4'd1;
                    if (lost == 4'd15) begin state <= ACQ; off <= 6'd4; best <= 0; end
                end
                if (fill < 6'd3 || fill > 6'd61) begin state <= ACQ; off <= 6'd4; best <= 0; en_b <= 1'b0; end
            end
        end
    end
endmodule
`default_nettype wire
