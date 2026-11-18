// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL channel selection
// with one button, like the C5's BOOT button: a short press steps the
// channel within the band, a hold (0.6 s) steps the band (R, A, B, E, F, L).
// `valid` stays 0 until the first press, so the C5s keep their own saved
// channel until the operator chooses one on the FPGA.
`default_nettype none
module channel_buttons #(
    parameter CLK_HZ = 27000000
) (
    input  wire       clk,
    input  wire       btn_n,        // active low
    output reg  [5:0] index = 6'd0,
    output reg        valid = 1'b0
);
    localparam DEB = CLK_HZ / 100;          // 10 ms debounce
    localparam HOLD = CLK_HZ / 10 * 6;      // 0.6 s
    reg [1:0]  s = 2'b11;
    reg        st = 1'b0;                   // debounced pressed
    reg [24:0] cnt = 0, held = 0;
    reg        long_done = 1'b0;
    always @(posedge clk) begin
        s <= {s[0], ~btn_n};
        if (s[1] != st) begin
            if (cnt == DEB) begin st <= s[1]; cnt <= 0; end else cnt <= cnt + 25'd1;
        end else cnt <= 0;
        if (st) begin
            if (held != HOLD) held <= held + 25'd1;
            else if (!long_done) begin                                     // hold: next band
                long_done <= 1'b1; valid <= 1'b1;
                index <= (index >= 6'd40) ? {3'd0, index[2:0]} : index + 6'd8;
            end
        end else begin
            if (held != 0 && !long_done) begin                             // short press: next channel
                valid <= 1'b1;
                index <= {index[5:3], index[2:0] + 3'd1};
            end
            held <= 0; long_done <= 1'b0;
        end
    end
endmodule
`default_nettype wire
