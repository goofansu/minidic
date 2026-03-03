"""Audio capture from microphone via sounddevice."""

from __future__ import annotations

import logging
import queue
from types import TracebackType

import numpy as np
import sounddevice as sd
import soxr

logger = logging.getLogger(__name__)

TARGET_RATE = 16_000  # What VAD/ASR expect
CHANNELS = 1
DTYPE = "int16"
BLOCKSIZE = 512  # 32ms chunks at 16kHz


def int16_to_float32(audio: np.ndarray) -> np.ndarray:
    """Convert int16 audio samples to float32 in [-1, 1]."""
    return audio.astype(np.float32) / 32768.0


def _refresh_portaudio() -> None:
    """Terminate and reinitialize PortAudio to pick up newly connected devices.

    PortAudio captures the device list at initialization time.  Calling
    Pa_Terminate / Pa_Initialize forces a fresh enumeration so that devices
    connected after the process started (e.g. Bluetooth headsets, USB mics)
    are visible to subsequent ``sd.query_devices`` / ``sd.InputStream`` calls.

    Uses the private ``sd._terminate`` / ``sd._initialize`` API — there is no
    public equivalent.  Both symbols have been stable since sounddevice 0.5.1
    and the project pins ``sounddevice>=0.5.5``.  If either call raises, the
    exception is re-raised so the caller receives a clear error rather than a
    deferred cryptic PortAudio failure (a partial reinit — e.g. terminate
    succeeded but initialize failed — leaves PortAudio uninitialized and must
    not be silently swallowed).
    """
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        logger.warning("PortAudio reinit failed", exc_info=True)
        raise


def _get_device_samplerate(device: int | str | None) -> float:
    """Query the native sample rate for the given input device."""
    info = sd.query_devices(device, kind="input")
    return float(info["default_samplerate"])


class AudioStream:
    """Captures audio from the microphone and pushes 16 kHz chunks to a queue.

    If the device's native sample rate differs from 16 kHz, audio is
    captured at the native rate and resampled with libsoxr.

    Usage::

        with AudioStream() as stream:
            while True:
                chunk = stream.read()  # np.ndarray int16, shape (blocksize,)
                ...

    Parameters
    ----------
    blocksize:
        Number of *output* samples per chunk at 16 kHz (default 512 = 32 ms).
    device:
        Input device index or name.  ``None`` uses the system default.
    """

    def __init__(
        self,
        blocksize: int = BLOCKSIZE,
        device: int | str | None = None,
    ) -> None:
        self.blocksize = blocksize
        self.device = device
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None

        # Determined at start() time
        self._native_rate: float = 0
        self._resampler: soxr.ResampleStream | None = None
        self._resample_buf: np.ndarray = np.array([], dtype=np.float32)

    # -- callback ----------------------------------------------------------

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logger.warning("sounddevice status: %s", status)

        # indata shape is (blocksize, 1) int16; flatten to (blocksize,).
        raw = indata[:, 0].copy()

        if self._resampler is not None:
            # Resample to 16 kHz.  soxr expects float32/float64.
            f32 = raw.astype(np.float32)
            resampled = self._resampler.resample_chunk(f32)
            # Buffer resampled samples and emit fixed-size chunks.
            self._resample_buf = np.concatenate([self._resample_buf, resampled])
            while len(self._resample_buf) >= self.blocksize:
                chunk = self._resample_buf[: self.blocksize]
                self._resample_buf = self._resample_buf[self.blocksize :]
                # Convert back to int16 for consistency
                self._queue.put_nowait(
                    np.clip(chunk, -32768, 32767).astype(np.int16)
                )
        else:
            self._queue.put_nowait(raw)

    # -- internal helpers --------------------------------------------------

    def _do_open(self) -> None:
        """Query device info, configure resampling, and open the PortAudio stream.

        Separated from ``start()`` so the try-on-failure retry in ``start()``
        can call it without duplicating the setup logic.  Caller must ensure
        ``self._stream is None`` before calling.
        """
        self._native_rate = _get_device_samplerate(self.device)
        needs_resample = abs(self._native_rate - TARGET_RATE) > 1

        if needs_resample:
            self._resampler = soxr.ResampleStream(
                self._native_rate,
                TARGET_RATE,
                num_channels=1,
                dtype=np.float32,
            )
            self._resample_buf = np.array([], dtype=np.float32)
            # Capture blocksize scaled to native rate
            native_blocksize = int(self.blocksize * self._native_rate / TARGET_RATE)
        else:
            self._resampler = None
            native_blocksize = self.blocksize

        stream = sd.InputStream(
            samplerate=self._native_rate,
            blocksize=native_blocksize,
            device=self.device,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=self._callback,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self._stream = stream
        logger.info(
            "Audio stream started  native_rate=%d  target_rate=%d  "
            "blocksize=%d  resample=%s  device=%s",
            int(self._native_rate),
            TARGET_RATE,
            self.blocksize,
            needs_resample,
            self.device or "default",
        )

    # -- public API --------------------------------------------------------

    def start(self) -> None:
        """Open and start the audio stream.

        On the first attempt the stream is opened against the current
        PortAudio device list.  If that fails (e.g. the user connected a new
        device since the process started and PortAudio's list is stale),
        PortAudio is reinitialized — which forces CoreAudio to re-enumerate
        devices — and the open is retried once.  The retry failure propagates
        to the caller.
        """
        if self._stream is not None:
            return

        try:
            self._do_open()
        except Exception as exc:
            logger.warning(
                "Audio stream open failed (%s); reinitializing PortAudio and retrying",
                exc,
            )
            _refresh_portaudio()
            self._do_open()  # propagates on second failure

    def stop(self) -> None:
        """Stop and close the audio stream."""
        if self._stream is None:
            return
        self._stream.stop()
        self._stream.close()
        self._stream = None
        self._resampler = None
        logger.info("Audio stream stopped")

    def read(self, timeout: float | None = None) -> np.ndarray:
        """Block until the next audio chunk is available.

        Returns an int16 numpy array of shape ``(blocksize,)``.

        Raises ``queue.Empty`` if *timeout* expires.
        """
        return self._queue.get(timeout=timeout)

    @property
    def queue(self) -> queue.Queue[np.ndarray]:
        """Direct access to the underlying chunk queue."""
        return self._queue

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> AudioStream:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.stop()
