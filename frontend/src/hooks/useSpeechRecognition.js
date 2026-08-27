import { useCallback, useRef, useState } from "react";

// Uses the browser's native SpeechRecognition API for transcription (no external
// speech vendor / API key required) and a separate mic stream + AnalyserNode purely
// for the waveform visualization, ported from the original Angular component's
// canvas drawing logic.
export function useSpeechRecognition() {
  const [isRecording, setIsRecording] = useState(false);
  const canvasRef = useRef(null);

  const recognitionRef = useRef(null);
  const finalTranscriptRef = useRef("");

  const streamRef = useRef(null);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const dataArrayRef = useRef(null);
  const animationIdRef = useRef(null);
  const angleRef = useRef(0);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const analyser = analyserRef.current;
    if (!canvas || !analyser) return;
    const ctx = canvas.getContext("2d");

    animationIdRef.current = requestAnimationFrame(draw);

    if (!dataArrayRef.current || dataArrayRef.current.length !== analyser.frequencyBinCount) {
      dataArrayRef.current = new Uint8Array(analyser.frequencyBinCount);
    }
    analyser.getByteTimeDomainData(dataArrayRef.current);

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    const baseRadius = Math.min(canvas.width, canvas.height) / 2 - 6;
    const data = dataArrayRef.current;

    ctx.lineWidth = 2;
    ctx.strokeStyle = "#2563eb";
    ctx.beginPath();
    const angleStep = (Math.PI * 2) / data.length;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      const radius = baseRadius + v * (baseRadius * 0.3);
      const angle = i * angleStep + angleRef.current;
      const x = centerX + radius * Math.cos(angle);
      const y = centerY + radius * Math.sin(angle);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.stroke();
    angleRef.current += 0.02;
  }, []);

  const startWaveform = useCallback(async () => {
    try {
      streamRef.current = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)();
      analyserRef.current = audioContextRef.current.createAnalyser();
      analyserRef.current.fftSize = 256;
      const source = audioContextRef.current.createMediaStreamSource(streamRef.current);
      source.connect(analyserRef.current);
      draw();
    } catch {
      // Waveform is a nice-to-have; recognition can still work without mic access
      // for the visualization stream (e.g. permission already granted to SpeechRecognition).
    }
  }, [draw]);

  const stopWaveform = useCallback(() => {
    if (animationIdRef.current) cancelAnimationFrame(animationIdRef.current);
    animationIdRef.current = null;
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => {});
      audioContextRef.current = null;
    }
    analyserRef.current = null;
  }, []);

  const start = useCallback(
    (onTranscript) => {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRecognition) {
        alert("Speech recognition isn't supported in this browser. Try Chrome or Edge.");
        return;
      }

      finalTranscriptRef.current = "";
      const recognition = new SpeechRecognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = "en-US";

      recognition.onresult = (event) => {
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const transcript = event.results[i][0].transcript;
          if (event.results[i].isFinal) {
            finalTranscriptRef.current += transcript + " ";
          } else {
            interim += transcript;
          }
        }
        onTranscript(finalTranscriptRef.current + interim);
      };
      recognition.onerror = () => {};
      recognition.start();

      recognitionRef.current = recognition;
      setIsRecording(true);
      startWaveform();
    },
    [startWaveform],
  );

  const stop = useCallback(() => {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
    }
    stopWaveform();
    setIsRecording(false);
  }, [stopWaveform]);

  return { isRecording, start, stop, canvasRef };
}
