{
  "id": "caebfe8a",
  "title": "Step 2: Audio capture module (audio.py)",
  "tags": [
    "implementation",
    "audio"
  ],
  "status": "done",
  "created_at": "2026-03-01T08:49:57.409Z"
}

Implemented `src/minidic/audio.py`:

- `AudioStream` class with context manager and start/stop interface
- `sounddevice.InputStream` at 16kHz mono int16, blocksize=512 (32ms chunks)
- Callback pushes flattened `(blocksize,)` int16 chunks to `queue.Queue`
- `status` warnings logged (overflow etc.) without crashing
- `int16_to_float32()` conversion utility
- `read(timeout=)` blocking method + direct `.queue` access
- Constants: `SAMPLE_RATE=16000`, `CHANNELS=1`, `DTYPE="int16"`, `BLOCKSIZE=512`
- Tested: int16_to_float32 conversion, stream open/capture/close cycle
