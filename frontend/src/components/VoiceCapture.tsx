/**
 * VoiceCapture — microphone input + morning briefing TTS trigger.
 *
 * Strategy (two-layer fallback):
 *  1. PRIMARY: Record audio → POST /voice/upload → OpenAI Whisper STT
 *  2. FALLBACK: If server returns "no_api_key", use browser's built-in
 *     Web Speech API (SpeechRecognition) — works without any API key.
 *
 * Morning Briefing: calls POST /voice/morning-briefing and reads the
 * result aloud via browser's SpeechSynthesis if ElevenLabs is unavailable.
 */

import { useRef, useState, useCallback, useEffect } from "react";
import { Mic, MicOff, Volume2, Loader2, Radio, CheckCircle } from "lucide-react";
import { api } from "../api";

interface VoiceCaptureProps {
  onTranscript: (text: string) => void;
  onToast?: (title: string, body: string, type?: "info" | "warning" | "urgent") => void;
}

type RecordState = "idle" | "listening" | "recording" | "uploading" | "done" | "error";
type BriefingState = "idle" | "loading" | "done" | "error";

// ── Web Speech API types ──────────────────────────────────────────────────
declare global {
  interface Window {
    SpeechRecognition: any;
    webkitSpeechRecognition: any;
  }
}

function getSpeechRecognition(): any {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

export function VoiceCapture({ onTranscript, onToast }: VoiceCaptureProps) {
  const [recordState, setRecordState] = useState<RecordState>("idle");
  const [briefingState, setBriefingState] = useState<BriefingState>("idle");
  const [statusMsg, setStatusMsg] = useState("");
  const [seconds, setSeconds] = useState(0);
  const [useWebSpeech, setUseWebSpeech] = useState(false); // set true after first no_api_key response

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const recognitionRef = useRef<any>(null);

  // Detect browser Web Speech API support on mount
  const hasBrowserSpeech = !!getSpeechRecognition();

  // ── Browser Web Speech API (fallback) ──────────────────────────────────

  const startBrowserSpeech = useCallback(() => {
    const SpeechRecognition = getSpeechRecognition();
    if (!SpeechRecognition) {
      setRecordState("error");
      setStatusMsg("Your browser does not support speech recognition. Please type your command.");
      return;
    }
    const recognition = new SpeechRecognition();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.continuous = false;

    recognition.onstart = () => {
      setRecordState("listening");
      setStatusMsg("Listening… speak now");
    };

    recognition.onresult = (event: any) => {
      const transcript: string = event.results[0][0].transcript;
      setRecordState("done");
      setStatusMsg(`Heard: "${transcript.slice(0, 80)}${transcript.length > 80 ? "…" : ""}"`);
      onTranscript(transcript);
      onToast?.("Voice Captured", "Transcript injected into Capture box.", "info");
      setTimeout(() => { setRecordState("idle"); setStatusMsg(""); setSeconds(0); }, 4000);
    };

    recognition.onerror = (event: any) => {
      const msg =
        event.error === "not-allowed"
          ? "Microphone access denied. Allow mic in browser settings."
          : event.error === "no-speech"
          ? "No speech detected. Try again."
          : `Speech error: ${event.error}`;
      setRecordState("error");
      setStatusMsg(msg);
      onToast?.("Voice Error", msg, "warning");
    };

    recognition.onend = () => {
      if (recordState === "listening") {
        setRecordState("idle");
      }
    };

    recognitionRef.current = recognition;
    recognition.start();
    setSeconds(0);
    timerRef.current = setInterval(() => setSeconds((s) => s + 1), 1000);
  }, [onTranscript, onToast, recordState]);

  const stopBrowserSpeech = useCallback(() => {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
    }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
  }, []);

  // ── MediaRecorder → Whisper (primary) ──────────────────────────────────

  const startRecording = useCallback(async () => {
    // If we already know Whisper isn't configured, skip straight to browser
    if (useWebSpeech) {
      startBrowserSpeech();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }

        const blob = new Blob(chunksRef.current, { type: mimeType || "audio/webm" });
        if (blob.size < 500) {
          setRecordState("error");
          setStatusMsg("Recording too short — please try again.");
          return;
        }
        setRecordState("uploading");
        setStatusMsg("Transcribing with Whisper…");
        try {
          const result = await api.voiceUpload(blob);

          if (result.status === "no_api_key") {
            // Switch to browser fallback permanently for this session
            setUseWebSpeech(true);
            setRecordState("idle");
            setStatusMsg("Using browser speech recognition (no OPENAI_API_KEY set).");
            onToast?.("Voice Mode", "Using browser speech recognition. Set OPENAI_API_KEY for Whisper.", "info");
            // Immediately start browser speech so user doesn't need to click again
            setTimeout(() => startBrowserSpeech(), 300);
            return;
          }

          if (result.status === "transcription_failed" || !result.transcript) {
            setRecordState("error");
            setStatusMsg(result.message ?? "Transcription failed. Please try again.");
            onToast?.("Voice Error", result.message ?? "Transcription failed.", "warning");
          } else {
            setRecordState("done");
            setStatusMsg(`✓ Transcript: "${result.transcript.slice(0, 70)}${result.transcript.length > 70 ? "…" : ""}"`);
            onTranscript(result.transcript);
            onToast?.("Voice Captured", "Transcript injected into Capture box.", "info");
            setTimeout(() => { setRecordState("idle"); setStatusMsg(""); setSeconds(0); }, 4000);
          }
        } catch (err) {
          // Network/server error — fall back to browser speech
          setUseWebSpeech(true);
          setRecordState("idle");
          setStatusMsg("Server unavailable — switching to browser speech.");
          setTimeout(() => startBrowserSpeech(), 300);
        }
      };

      recorder.start(250);
      mediaRecorderRef.current = recorder;
      setRecordState("recording");
      setStatusMsg("Recording…");
      setSeconds(0);
      timerRef.current = setInterval(() => setSeconds((s) => s + 1), 1000);
    } catch {
      setRecordState("error");
      setStatusMsg("Microphone access denied. Allow mic in browser settings.");
    }
  }, [useWebSpeech, startBrowserSpeech, onTranscript, onToast]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
  }, []);

  const toggleRecording = () => {
    if (recordState === "recording" || recordState === "listening") {
      stopRecording();
      stopBrowserSpeech();
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
      setRecordState("idle");
      setStatusMsg("");
    } else if (recordState === "idle" || recordState === "done" || recordState === "error") {
      startRecording();
    }
  };

  // ── Morning Briefing ─────────────────────────────────────────────────────

  const triggerBriefing = async () => {
    setBriefingState("loading");
    setStatusMsg("Building your agenda…");

    // IMPORTANT: Chrome blocks SpeechSynthesis unless initiated from a synchronous
    // user gesture. We create and queue a placeholder utterance NOW (sync),
    // then update its text once we get the API response.
    let pendingText = "Fetching your morning briefing…";
    const utterance = new SpeechSynthesisUtterance(pendingText);
    utterance.rate = 0.92;
    utterance.pitch = 1.0;
    utterance.volume = 1.0;

    // Speak placeholder immediately to unlock the audio context
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
      // We'll restart with real text after fetch — but first we need to
      // call speak() synchronously to register the user-gesture unlock.
      // We use a very short intro that buys us time:
      const unlock = new SpeechSynthesisUtterance("Good morning!");
      unlock.volume = 0;  // silent — just unlocks audio context
      window.speechSynthesis.speak(unlock);
    }

    try {
      const result = await api.morningBriefing();
      let briefText = "";

      if (result.status === "no_items") {
        briefText = "Your agenda is clear. No pending items right now.";
        setBriefingState("done");
        setStatusMsg("Your agenda is clear — nothing to brief!");
        onToast?.("Morning Briefing", "No pending items right now.", "info");
      } else {
        briefText = result.text ?? `You have ${result.items} agenda item${result.items === 1 ? "" : "s"}.`;
        setBriefingState("done");
        setStatusMsg(`🔊 Briefing: ${result.items} item${result.items === 1 ? "" : "s"}`);
        onToast?.("Morning Briefing", briefText.slice(0, 140), "info");
      }

      // Now speak the real text
      if (window.speechSynthesis) {
        window.speechSynthesis.cancel();
        const utt = new SpeechSynthesisUtterance(briefText);
        utt.rate = 0.92;
        utt.pitch = 1.0;
        utt.volume = 1.0;
        window.speechSynthesis.speak(utt);
      }

      setTimeout(() => { setBriefingState("idle"); setStatusMsg(""); }, 10000);
    } catch (err) {
      setBriefingState("error");
      const msg = err instanceof Error ? err.message : "Briefing failed.";
      setStatusMsg(msg);
      onToast?.("Briefing Error", msg, "warning");
      setTimeout(() => { setBriefingState("idle"); setStatusMsg(""); }, 4000);
    }
  };

  // Browser Text-to-Speech (SpeechSynthesis) — free, no API key needed
  function speakBrowser(text: string) {
    if (!window.speechSynthesis) return;
    window.speechSynthesis.cancel(); // stop anything playing
    const utt = new SpeechSynthesisUtterance(text);
    utt.rate = 0.95;
    utt.pitch = 1.0;
    utt.volume = 1.0;
    window.speechSynthesis.speak(utt);
  }

  // Cleanup on unmount
  useEffect(() => () => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (recognitionRef.current) recognitionRef.current.stop();
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
  }, []);

  // ── Derived UI ──────────────────────────────────────────────────────────

  const isActive = recordState === "recording" || recordState === "listening";
  const isUploading = recordState === "uploading";
  const isDone = recordState === "done";
  const isBriefingLoading = briefingState === "loading";
  const modeLabel = useWebSpeech || !window.MediaRecorder ? "Browser" : "Whisper";

  function formatTime(s: number): string {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  }

  return (
    <div className="voice-capture-bar">
      {/* Mic button */}
      <button
        id="voice-mic-btn"
        className={`btn voice-mic-btn ${isActive ? "recording" : ""} ${isDone ? "done" : ""}`}
        onClick={toggleRecording}
        disabled={isUploading}
        title={isActive ? "Stop recording" : `Record voice note (${modeLabel} STT)`}
      >
        {isUploading ? (
          <Loader2 size={16} className="spin" />
        ) : isDone ? (
          <CheckCircle size={16} />
        ) : isActive ? (
          <MicOff size={16} />
        ) : (
          <Mic size={16} />
        )}
        <span className="voice-btn-label">
          {isUploading
            ? "Transcribing…"
            : isDone
            ? "Done!"
            : isActive
            ? `Stop  ${formatTime(seconds)}`
            : `Voice Input`}
        </span>
        {isActive && <span className="voice-pulse" />}
      </button>

      {/* Mode indicator */}
      <span className="voice-mode-pill" title={useWebSpeech ? "Using browser speech recognition" : "Using OpenAI Whisper"}>
        {useWebSpeech ? "🌐 Browser" : "✦ Whisper"}
      </span>

      {/* Morning Briefing button */}
      <button
        id="voice-briefing-btn"
        className={`btn voice-briefing-btn ${isBriefingLoading ? "loading" : ""}`}
        onClick={triggerBriefing}
        disabled={isBriefingLoading}
        title="Read aloud your top 5 agenda items"
      >
        {isBriefingLoading ? (
          <Loader2 size={16} className="spin" />
        ) : briefingState === "done" ? (
          <Radio size={16} />
        ) : (
          <Volume2 size={16} />
        )}
        <span className="voice-btn-label">
          {isBriefingLoading ? "Building…" : "Morning Briefing"}
        </span>
      </button>

      {/* Status message */}
      {statusMsg && (
        <span className={`voice-status ${recordState === "error" || briefingState === "error" ? "error" : "info"}`}>
          {statusMsg}
        </span>
      )}
    </div>
  );
}
