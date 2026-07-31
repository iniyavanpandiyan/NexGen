import sys, os, torch

try:
    from chatterbox.tts import ChatterboxTTS
except Exception as e:
    print("IMPORT_FAIL:", repr(e)); sys.exit(2)

print("torch:", torch.__version__, "cuda available:", torch.cuda.is_available())
device = "cuda" if torch.cuda.is_available() else "cpu"
print("device:", device)

model = ChatterboxTTS.from_pretrained(device=device)
print("model loaded. sample_rate:", model.sr)

text = "Hey, this is a test of Chatterbox text to speech running locally on the RTX 3060. If you can hear this clearly, our voice pipeline is ready for the explainer videos."

out_dir = "/home/fiipadmin/workspace/NexGen/pipeline/rendered/chatterbox-test"
os.makedirs(out_dir, exist_ok=True)
wav_path = os.path.join(out_dir, "test.wav")

wav = model.generate(text, exaggeration=0.5, temperature=0.8, cfg_weight=0.5)
import soundfile as sf
sf.write(wav_path, wav, model.sr)
print("WROTE_WAV:", wav_path, "shape:", tuple(wav.shape), "dur_s:", round(wav.shape[0]/model.sr, 2))
