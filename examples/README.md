# Example circuits

Open any of these with **File → Open Netlist** (or drag the `.cir` onto the
window), then run the simulation. Every deck is self-contained — device models
are defined inline, so nothing external needs to be installed. The `.model`
cards use generic, illustrative parameters; they are not tied to any specific
real-world part.

All decks below were verified to parse, converge, and produce data under
ngspice-46.

## simple/

| File | What it shows | Analyses |
|------|---------------|----------|
| `voltage_divider.cir` | Resistive divider, `Vout = Vin·R2/(R1+R2)` | `.op`, `.dc` |
| `rc_step.cir` | RC charging, τ = R·C = 100 µs step response | `.tran` |
| `halfwave_rectifier.cir` | Diode passing positive half-cycles of a sine | `.tran` |

## complex/

| File | What it shows | Analyses |
|------|---------------|----------|
| `ce_amplifier.cir` | BJT common-emitter amplifier (bias, gain, bandwidth) | `.ac`, `.tran`, `.op` |
| `sallen_key_lpf.cir` | 2nd-order Sallen-Key active low-pass, fc ≈ 1.6 kHz | `.ac`, `.tran` |
| `astable_multivibrator.cir` | Free-running cross-coupled BJT square-wave oscillator | `.tran` |

> **Note on `astable_multivibrator.cir`:** each regenerative switching edge
> forces very fine timesteps, so even a short run produces tens of thousands of
> points. The `.tran` window is deliberately kept to ~1 ms (a couple of cycles)
> to keep the dataset manageable; lengthen it only if you are prepared for a
> much larger point count.
