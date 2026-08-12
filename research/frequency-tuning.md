# Frequency tuning research

C5VRX is not limited to Band A. The ESP32-C5 receive hardware covers the 5 GHz region used by most legacy analog FPV channels, but the clean public Wi-Fi API only exposes standard Wi-Fi channel centers.

## FPV frequency set

The firmware channel database mirrors the classic Betaflight factory table:

- **A:** 5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725 MHz
- **B:** 5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866 MHz
- **E:** 5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945 MHz
- **F:** 5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880 MHz
- **R:** 5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917 MHz

The current C5 hardware target is **5645–5885 MHz**. Channels above 5885 MHz are deliberately marked out-of-window rather than pretending they are supported.

## Bootstrap strategy

For every requested FPV frequency, C5VRX computes the nearest normal 5 GHz Wi-Fi center. That gives us two useful modes:

1. **Exact overlap** — no PLL hack required. Example: 5805 MHz = Wi-Fi channel 161.
2. **Near-center HT40 experiment** — start on the closest Wi-Fi center and observe a nearby analog carrier inside the wide receive passband. Example: RaceBand 5 at 5806 MHz is only +1 MHz from channel 161.

This is valuable even before arbitrary-frequency tuning works.

## Direct frequency hook

Current ESP32-C5 PHY symbol inventories include several frequency-control functions, notably:

```text
phy_set_freq
phy_set_chanfreq
phy_set_rfpll_freq
phy_set_channel_rfpll_freq_new
phy_write_chan_freq
phy_freq_to_chan
phy_chan_to_freq
```

A separate open reverse-engineering project, `esp-hosted-open`, currently declares and calls:

```c
extern void phy_set_freq(int freq_mhz) __attribute__((weak));
```

C5VRX now carries the same call shape behind a weak symbol and an explicit experimental switch. The weak reference means the project still links when the symbol is absent.

### Important

The symbol name and apparent prototype are strong leads, not a vendor-supported API contract. Until tested on real ESP32-C5 hardware, direct retuning stays **opt-in**.

In `main/c5vrx_main.c`:

```c
#define C5VRX_EXPERIMENTAL_DIRECT_TUNE 0
```

Change it to `1` only when intentionally testing the direct-frequency path.

## Validation plan

For each target frequency:

1. Start RF-test RX on the nearest official Wi-Fi channel.
2. Record RSSI/noise/dump behavior with a CW source or VTX at the Wi-Fi center.
3. Move the RF source 1, 5, 10 and 15 MHz away and record the response.
4. Enable `phy_set_freq(target_mhz)` and repeat.
5. Verify the actual center moved using a known narrowband RF source rather than inferring from packet counters.
6. Only after that use the hook for analog FPV captures.

The best first target is **R5 / 5806 MHz**, because it is just 1 MHz from the known-safe 5805 MHz center. A successful one-megahertz shift is much easier to distinguish from a completely broken retune.
