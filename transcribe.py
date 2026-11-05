"""Machine transcripts for the videos in media/.

For each media/<id>.mp4 without a transcript, writes:
  media/<id>.vtt  captions the player can show
  media/<id>.txt  the same words as paragraphs, one per blank-line block
"""

from pathlib import Path

from faster_whisper import WhisperModel

MEDIA = Path(__file__).resolve().parent / "media"
PAUSE_FOR_NEW_PARAGRAPH = 1.5   # seconds of silence
MAX_PARAGRAPH_SECONDS = 75


def stamp(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def main():
    todo = [v for v in sorted(MEDIA.glob("*.mp4")) if not v.with_suffix(".txt").exists()]
    if not todo:
        print("No new videos.")
        return
    model = WhisperModel("medium.en", device="cpu", compute_type="int8")
    for video in todo:
        print(f"Transcribing {video.name}")
        segments, _ = model.transcribe(str(video), beam_size=5, vad_filter=True,
                                       condition_on_previous_text=False)
        segments = [s for s in segments if s.text.strip()]

        vtt = ["WEBVTT", ""]
        for s in segments:
            vtt += [f"{stamp(s.start)} --> {stamp(s.end)}", s.text.strip(), ""]
        video.with_suffix(".vtt").write_text("\n".join(vtt))

        paragraphs, current, began, last_end = [], [], None, None
        for s in segments:
            if current and (s.start - last_end > PAUSE_FOR_NEW_PARAGRAPH
                            or s.start - began > MAX_PARAGRAPH_SECONDS):
                paragraphs.append(" ".join(current))
                current, began = [], None
            if began is None:
                began = s.start
            current.append(s.text.strip())
            last_end = s.end
        if current:
            paragraphs.append(" ".join(current))
        video.with_suffix(".txt").write_text("\n\n".join(paragraphs) + "\n")
        print(f"  {len(segments)} segments, {len(paragraphs)} paragraphs")


if __name__ == "__main__":
    main()
