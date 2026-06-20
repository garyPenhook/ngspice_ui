"""Pure waveform math — FFT, group delay.  No GUI dependency."""

from __future__ import annotations

import numpy as np


def compute_fft(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """FFT of a real transient waveform with non-uniform time steps.

    Resamples *y(x)* to a uniform grid before computing the FFT so that
    frequency bins are evenly spaced.

    Returns ``(freqs, mag_db)`` where *freqs* starts at the first non-DC bin
    and *mag_db* is the single-sided magnitude spectrum in dB.

    Raises ValueError if the arrays are too short or mismatched.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(x)
    if n < 4 or len(y) != n:
        raise ValueError("FFT requires at least 4 matched samples")
    t0, t1 = float(x[0]), float(x[-1])
    # np.interp silently produces garbage for a non-monotonic xp, so verify the
    # whole axis is strictly increasing — not just the endpoints.
    if not np.all(np.diff(x) > 0):
        raise ValueError("Time axis must be strictly increasing")
    t_uniform = np.linspace(t0, t1, n)
    y_uniform = np.interp(t_uniform, x, y)
    dt = (t1 - t0) / (n - 1)
    freqs = np.fft.rfftfreq(n, d=dt)
    spectrum = np.fft.rfft(y_uniform)
    # Single-sided amplitude: divide by n then double the bins that have a
    # mirror-image negative-frequency partner so a unit-amplitude sine reads
    # ≈ 0 dB. The DC bin (index 0) is never doubled. A Nyquist bin exists only
    # for *even*-length inputs (at the final index) and must also stay
    # un-doubled; an odd-length rfft has no Nyquist bin, so its final bin *does*
    # have a partner and must be doubled. Doubling [1:-1] unconditionally left
    # the last bin of every odd-length FFT 6.02 dB low.
    mag = np.abs(spectrum) / n
    if n % 2 == 0:
        mag[1:-1] *= 2  # even length: exclude DC (0) and Nyquist (last)
    else:
        mag[1:] *= 2  # odd length: exclude DC only — no Nyquist bin
    mag_db = 20.0 * np.log10(mag + 1e-300)
    # Drop DC bin (index 0)
    return freqs[1:], mag_db[1:]


def compute_group_delay(freq: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Group delay in seconds from complex frequency-domain data *H(f)*.

    ``τ(f) = -d(∠H)/dω``
    """
    freq = np.asarray(freq, dtype=float)
    h = np.asarray(h, dtype=complex)
    phase_rad = np.unwrap(np.angle(h))
    omega = 2.0 * np.pi * freq
    return -np.gradient(phase_rad, omega)
