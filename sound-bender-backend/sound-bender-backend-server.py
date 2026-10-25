"""Sound Bender processing server.

Analyzes audio for BPM and key, and bends a voice to a new tempo and key with
Rubber Band (R3 engine) and ffmpeg. Runs on Railway from the Dockerfile in this folder.
"""
import asyncio
import os
import shutil
import subprocess
import tempfile

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

MAX_BYTES = 40 * 1024 * 1024
MAX_SECONDS = 600
ANALYZE_SECONDS = 120
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

app = FastAPI(title="sound-bender-backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
gate = asyncio.Semaphore(1)


@app.get("/")
def health():
    tools = {t: bool(shutil.which(t)) for t in ("ffmpeg", "ffprobe", "rubberband")}
    return {"service": "sound-bender-backend", "ok": all(tools.values()), "tools": tools}


async def save_upload(upload: UploadFile, folder: str) -> str:
    path = os.path.join(folder, "source")
    size = 0
    with open(path, "wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                raise HTTPException(413, "file is larger than the server allows")
            out.write(chunk)
    if size == 0:
        raise HTTPException(400, "empty file")
    return path


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise HTTPException(500, (proc.stderr or proc.stdout or "command failed")[-400:])
    return proc.stdout


def duration_of(path: str) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    try:
        return float(out.strip())
    except ValueError:
        raise HTTPException(400, "could not read that audio file")


def to_wav(src: str, dst: str, rate: int, channels: int, seconds: float = 0):
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", str(channels), "-ar", str(rate)]
    if seconds:
        cmd += ["-t", str(seconds)]
    run(cmd + ["-c:a", "pcm_s16le", dst])


def detect(path: str) -> dict:
    import librosa  # loaded here so the health check answers fast on boot

    y, sr = librosa.load(path, sr=22050, mono=True)
    if y.size < sr * 3:
        raise HTTPException(400, "audio is too short to analyze")
    harmonic, percussive = librosa.effects.hpss(y)
    tempo, _ = librosa.beat.beat_track(y=percussive, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr).mean(axis=1)
    best = None
    for tonic in range(12):
        rolled = np.roll(chroma, -tonic)
        for mode, profile in (("major", MAJOR), ("minor", MINOR)):
            score = float(np.corrcoef(rolled, profile)[0, 1])
            if best is None or score > best[0]:
                best = (score, tonic, mode)
    score, tonic, mode = best
    return {
        "bpm": round(bpm, 1),
        "tonic": tonic,
        "mode": mode,
        "key": NAMES[tonic] + " " + mode,
        "confidence": round(score, 3),
    }


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    async with gate:
        folder = tempfile.mkdtemp(prefix="bender-")
        try:
            src = await save_upload(file, folder)
            seconds = await asyncio.to_thread(duration_of, src)
            wav = os.path.join(folder, "analyze.wav")
            await asyncio.to_thread(to_wav, src, wav, 22050, 1, ANALYZE_SECONDS)
            found = await asyncio.to_thread(detect, wav)
            found["duration"] = round(seconds, 2)
            return found
        finally:
            shutil.rmtree(folder, ignore_errors=True)


@app.post("/bend")
async def bend(
    file: UploadFile = File(...),
    tempo: float = Form(1.0),
    semitones: float = Form(0.0),
    formant: bool = Form(True),
):
    if not 0.5 <= tempo <= 2.0:
        raise HTTPException(400, "tempo change must stay between half speed and double speed")
    if not -12 <= semitones <= 12:
        raise HTTPException(400, "pitch shift must stay within one octave")
    async with gate:
        folder = tempfile.mkdtemp(prefix="bender-")
        try:
            src = await save_upload(file, folder)
            seconds = await asyncio.to_thread(duration_of, src)
            if seconds > MAX_SECONDS:
                raise HTTPException(413, "audio is longer than the server allows")
            wav_in = os.path.join(folder, "in.wav")
            wav_out = os.path.join(folder, "out.wav")
            mp3_out = os.path.join(folder, "sound-bender-bent.mp3")
            await asyncio.to_thread(to_wav, src, wav_in, 44100, 2)
            cmd = ["rubberband", "-3", "-q", "-T", f"{tempo:.6f}", "-p", f"{semitones:.4f}"]
            if formant:
                cmd.append("-F")
            await asyncio.to_thread(run, cmd + [wav_in, wav_out])
            await asyncio.to_thread(
                run, ["ffmpeg", "-y", "-v", "error", "-i", wav_out, "-c:a", "libmp3lame", "-b:a", "192k", mp3_out]
            )
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    return FileResponse(
        mp3_out,
        media_type="audio/mpeg",
        filename="sound-bender-bent.mp3",
        background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True),
    )
