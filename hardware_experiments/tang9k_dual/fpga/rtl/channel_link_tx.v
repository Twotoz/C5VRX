// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL channel link
// transmitter (C5 side: main/channel_link.h). One UART line (115200 8N1)
// to D9 of both C5s; the frame 0xC5, 'C', index, 0x79 ^ index repeats every
// ~20 ms while `valid`, so a C5 that reboots follows within one frame.
// INVERT drives the line inverted (idle low) for the one-transistor level
// shifter from a 1.8 V bank (Tang Nano 9K).
`default_nettype none
module channel_link_tx #(
    parameter CLK_HZ = 27000000,
    parameter BAUD   = 115200,
    parameter INVERT = 0
) (
    input  wire       clk,
    input  wire       valid,        // 0: line idle (the C5s keep their own channel)
    input  wire [5:0] index,        // band * 8 + channel, 0..47
    output wire       tx
);
    localparam DIV = (CLK_HZ + BAUD / 2) / BAUD;
    localparam GAP = CLK_HZ / 50;   // frame repeat, 20 ms
    reg [15:0] baud = 0;
    reg [3:0]  bitn = 0;            // 0 idle, 1 start, 2..9 data, 10 stop
    reg [1:0]  byten = 0;
    reg [7:0]  sh = 8'hFF;
    reg        line = 1'b1;
    reg [24:0] gap = 0;
    reg [5:0]  idx_l = 0;
    always @(posedge clk) begin
        if (bitn == 4'd0) begin
            line <= 1'b1;
            if (gap != 0) gap <= gap - 25'd1;
            else if (valid) begin
                idx_l <= index; byten <= 2'd0; bitn <= 4'd1; baud <= DIV - 1;
                sh <= 8'hC5; line <= 1'b0;                    // start bit of byte 0
            end
        end else if (baud != 0) baud <= baud - 16'd1;
        else begin
            baud <= DIV - 1;
            if (bitn <= 4'd8) begin line <= sh[0]; sh <= {1'b1, sh[7:1]}; bitn <= bitn + 4'd1; end
            else if (bitn == 4'd9) begin line <= 1'b1; bitn <= 4'd10; end   // stop bit
            else if (byten != 2'd3) begin                                      // next byte
                byten <= byten + 2'd1; bitn <= 4'd1; line <= 1'b0;
                sh <= (byten == 2'd0) ? 8'h43 : (byten == 2'd1) ? {2'b00, idx_l} : (8'h79 ^ {2'b00, idx_l});
            end else begin bitn <= 4'd0; gap <= GAP; end
        end
    end
    assign tx = INVERT ? ~line : line;
endmodule
`default_nettype wire
