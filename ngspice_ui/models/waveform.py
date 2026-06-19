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
    if t1 <= t0:
        raise ValueError("Time axis must be strictly increasing")
    t_uniform = np.linspace(t0, t1, n)
    y_uniform = np.interp(t_uniform, x, y)
    dt = (t1 - t0) / (n - 1)
    freqs = np.fft.rfftfreq(n, d=dt)
    spectrum = np.fft.rfft(y_uniform)
    # Single-sided amplitude: divide by n then double non-DC non-Nyquist bins
    # so that a unit-amplitude sine reads ≈ 0 dB.
    mag = np.abs(spectrum) / n
    mag[1:-1] *= 2
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
