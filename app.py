# Import queue to handle non-blocking audio data transfer between threads
import queue
# Import time to track word cooldowns and prevent duplicate double-triggering
import time
# Import torch to handle deep learning tensors and execute on GPU
import torch
# Import colorama to handle colored terminal text output
from colorama import Fore, Style, init
# Import sounddevice to handle continuous asynchronous microphone input
import sounddevice as sd
# Import numpy to handle raw audio float array conversion
import numpy as np
# Import the Hugging Face pipeline tool for automatic speech recognition
from transformers import pipeline

# Initialize colorama for automatic terminal style resetting
init(autoreset=True)

# Define target reference text for recitation and memorization
TARGET_TEXT = "the quick brown fox jumps over the lazy dog"

# Split cleaned target text into an ordered list of target words
target_words = TARGET_TEXT.lower().split()

# Select computing device: NVIDIA CUDA GPU if available, else CPU
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Using device: {device}")

# Load Whisper model onto local GPU VRAM
print("Loading Whisper model into GPU VRAM... Please wait.")
asr_pipeline = pipeline(
    "automatic-speech-recognition", 
    model="openai/whisper-base", 
    device=device
)

# Set standard audio sampling rate (16kHz required by Whisper)
SAMPLE_RATE = 16000

# Create a thread-safe queue to store raw audio stream data from callback
audio_queue = queue.Queue()

# Define sounddevice stream callback function to collect audio frames continuously
def audio_callback(indata, frames, time_info, status):
    if status:
        print(status, flush=True)
    # Put a copy of incoming float32 microphone data into queue
    audio_queue.put(indata.copy())

# Define common Whisper ghost/silent hallucinations to filter out automatically
HALLUCINATION_WORDS = {"thank", "you", "thanks", "watching", "subtitles", "for"}

print("\n--- Enhancement V8 (Deduplication & Noise Filter) Listening! Recite your text. Press Ctrl+C to stop. ---\n")

# Initialize word pointer to track progress through target paragraph
current_word_index = 0

# Variables to track and block duplicate triggers
last_processed_word = ""
last_processed_time = 0.0
COOLDOWN_SECONDS = 0.8  # Must wait at least 0.8 seconds before repeating the exact same word

# Open non-blocking audio input stream using sounddevice
with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32', callback=audio_callback, blocksize=int(SAMPLE_RATE * 0.5)):
    try:
        # Maintain a rolling array buffer for audio processing
        buffer_audio = np.array([], dtype=np.float32)
        
        while True:
            # Check if all target words in the paragraph have been processed
            if current_word_index >= len(target_words):
                print(Fore.GREEN + "\n\n🎉 Paragraph completed successfully! Great job!")
                break

            # Drain queued audio frames into local buffer array
            while not audio_queue.empty():
                chunk = audio_queue.get()
                buffer_audio = np.append(buffer_audio, chunk.flatten())

            # Process buffer once it accumulates at least 1.5 seconds of audio
            if len(buffer_audio) >= int(SAMPLE_RATE * 1.5):
                # Extract 1.5 seconds slice of audio for inference
                processing_chunk = buffer_audio[:int(SAMPLE_RATE * 1.5)]
                # Keep remaining samples in buffer to preserve stream continuity
                buffer_audio = buffer_audio[int(SAMPLE_RATE * 1.0):]

                # Strict volume threshold to ignore breathing and quiet room noise
                if np.max(np.abs(processing_chunk)) < 0.04:
                    continue

                # Run Whisper ASR model on GPU with temperature=0.0 to prevent creative hallucinations
                result = asr_pipeline(
                    {"raw": processing_chunk, "sampling_rate": SAMPLE_RATE},
                    generate_kwargs={"language": "en", "temperature": 0.0}
                )
                
                # Extract clean lowercase transcribed text string
                transcribed_text = result["text"].lower().strip()
                raw_spoken_words = transcribed_text.split()

                # Filter out single-letter noise artifacts (like [v]) and immediate chunk duplicates
                cleaned_spoken_words = []
                for w in raw_spoken_words:
                    clean_w = ''.join(c for c in w if c.isalnum())
                    # Keep word if it's length >= 2, or if it's a valid single-letter word ("a" or "i")
                    if len(clean_w) >= 2 or clean_w in {"a", "i"}:
                        # Prevent back-to-back duplicates inside the same transcription chunk
                        if not cleaned_spoken_words or clean_w != cleaned_spoken_words[-1]:
                            cleaned_spoken_words.append(clean_w)

                # Process deduplicated words extracted from current audio chunk
                for clean_word in cleaned_spoken_words:
                    # Filter out common Whisper ghost hallucinations automatically
                    if clean_word in HALLUCINATION_WORDS:
                        continue

                    # Check debounce cooldown to prevent duplicate double-triggering of the same word
                    current_time = time.time()
                    if clean_word == last_processed_word and (current_time - last_processed_time) < COOLDOWN_SECONDS:
                        continue

                    if current_word_index < len(target_words):
                        expected_word = target_words[current_word_index]

                        # Match case 1: Spoken word matches current expected target word
                        if clean_word == expected_word:
                            print(Fore.GREEN + clean_word, end=" ", flush=True)
                            last_processed_word = clean_word
                            last_processed_time = current_time
                            current_word_index += 1
                        
                        # Match case 2: Check if spoken word matches a future target word within 3 steps
                        elif clean_word in target_words[current_word_index + 1 : current_word_index + 4]:
                            match_offset = target_words[current_word_index + 1 : current_word_index + 4].index(clean_word) + 1
                            # Highlight intermediate words as missed
                            for i in range(match_offset):
                                print(Fore.RED + f"[Missed: {target_words[current_word_index + i]}]" + Style.RESET_ALL, end=" ", flush=True)
                            print(Fore.GREEN + clean_word, end=" ", flush=True)
                            last_processed_word = clean_word
                            last_processed_time = current_time
                            current_word_index += match_offset + 1
                        
                        # Match case 3: Unmatched extra word or noise
                        else:
                            print(Fore.RED + f"[{clean_word}]" + Style.RESET_ALL, end=" ", flush=True)

    except KeyboardInterrupt:
        print("\n\nStopping recording. Great job practicing with Enhancement V8!")