import { useEffect, useRef, useState } from "react";
import { handleAgent } from "../../api/agenticService.js";
import { synthesizeSpeech } from "../../api/ttsService.js";
import { useSpeechRecognition } from "../../hooks/useSpeechRecognition.js";
import "./AiAgenticChat.css";

const PLACEHOLDERS = [
  'Ask: "What was total demand last week?"',
  'Ask: "Which products are at risk of stockout?"',
  'Ask: "What is the forecast accuracy for the latest run?"',
  'Ask: "Show me open replenishment orders"',
];

function AudioMessage({ msg }) {
  const audioRef = useRef(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const audio = new Audio(msg.audioUrl);
    audioRef.current = audio;
    audio.ontimeupdate = () => {
      if (audio.duration) setProgress((audio.currentTime / audio.duration) * 100);
    };
    audio.onended = () => {
      setIsPlaying(false);
      setProgress(0);
    };
    return () => audio.pause();
  }, [msg.audioUrl]);

  const toggle = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
      setIsPlaying(false);
    } else {
      audio.play();
      setIsPlaying(true);
    }
  };

  return (
    <div className="audio-message">
      <button className="icon-btn" onClick={toggle} aria-label={isPlaying ? "Pause" : "Play"}>
        {isPlaying ? "⏸" : "▶"}
      </button>
      <div className="progress-wrapper">
        <div className="progress-bar" style={{ width: `${progress}%` }} />
      </div>
    </div>
  );
}

export default function AiAgenticChat({ currentUserRole, staffId = "" }) {
  const [isOverlayVisible, setIsOverlayVisible] = useState(false);
  const [chatMessages, setChatMessages] = useState([]);
  const [userQuestion, setUserQuestion] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [currentQueryStatus, setCurrentQueryStatus] = useState("thinking");
  const [placeholderIndex, setPlaceholderIndex] = useState(0);

  const chatContainerRef = useRef(null);
  const isUserNearBottomRef = useRef(true);
  const isMicTriggeredRef = useRef(false);
  const chatHistoryRef = useRef([]);
  const previousQuestionRef = useRef("");
  const previousAnswerRef = useRef("");
  const rotationIntervalRef = useRef(null);

  const { isRecording, start, stop, canvasRef } = useSpeechRecognition();

  useEffect(() => {
    startPlaceholderRotation();
    return () => stopPlaceholderRotation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (isUserNearBottomRef.current && chatContainerRef.current) {
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
    }
  }, [chatMessages, isLoading]);

  function startPlaceholderRotation() {
    stopPlaceholderRotation();
    rotationIntervalRef.current = setInterval(() => {
      setPlaceholderIndex((i) => (i + 1) % PLACEHOLDERS.length);
    }, 2500);
  }

  function stopPlaceholderRotation() {
    if (rotationIntervalRef.current) clearInterval(rotationIntervalRef.current);
  }

  function onChatScroll() {
    const el = chatContainerRef.current;
    if (!el) return;
    const threshold = 50;
    isUserNearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
  }

  function clearChat() {
    setChatMessages([]);
    chatHistoryRef.current = [];
    previousQuestionRef.current = "";
    previousAnswerRef.current = "";
  }

  async function sendQuestion(rawQuestion) {
    const question = (rawQuestion ?? userQuestion).trim();
    if (!question || isLoading) return;

    if (isRecording) stop();
    setUserQuestion("");
    isUserNearBottomRef.current = true;
    setIsLoading(true);
    setCurrentQueryStatus("thinking");

    setChatMessages((prev) => [...prev, { sender: "user", text: question, timestamp: new Date() }]);
    chatHistoryRef.current = [...chatHistoryRef.current, { role: "user", content: question }];

    const queryRequest = {
      question,
      previousQuestion: previousQuestionRef.current,
      previousAnswer: previousAnswerRef.current,
      previousSql: "",
      currentRole: currentUserRole,
      staffId: staffId || "",
      history: chatHistoryRef.current.slice(-12),
    };

    try {
      const data = await handleAgent(queryRequest);
      previousQuestionRef.current = data?.question || question;
      previousAnswerRef.current = data?.answer?.value || "";
      chatHistoryRef.current = [
        ...chatHistoryRef.current,
        { role: "assistant", content: data?.answer?.value || "" },
      ];

      const answerHtml = data?.answer?.value || "Please clarify what you're looking for.";

      if (isMicTriggeredRef.current) {
        setCurrentQueryStatus("observing");
        isMicTriggeredRef.current = false;
        try {
          const audioUrl = await synthesizeSpeech(stripHtml(answerHtml));
          setChatMessages((prev) => [
            ...prev,
            { sender: "ai", isAudio: true, audioUrl, timestamp: new Date() },
          ]);
        } catch {
          // TTS is best-effort; the text bubble below still carries the answer.
        }
      }

      const aiMessage = { sender: "ai", timestamp: new Date() };
      if (data?.actions?.length) {
        aiMessage.text = data?.answer?.value || "Please choose an option";
        aiMessage.actions = data.actions;
      } else {
        aiMessage.text = data?.answer?.value || "No response available";
      }
      setChatMessages((prev) => [...prev, aiMessage]);
      setCurrentQueryStatus("thinking");
    } catch {
      setChatMessages((prev) => [
        ...prev,
        {
          sender: "ai",
          text: "We're having difficulty understanding your request or fetching the data. Please rephrase your question and try again.",
          timestamp: new Date(),
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }

  function stripHtml(html) {
    const div = document.createElement("div");
    div.innerHTML = html;
    return div.textContent || div.innerText || "";
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!isLoading) sendQuestion();
    }
  }

  function startListening() {
    if (isRecording) {
      stop();
      return;
    }
    setIsOverlayVisible(true);
    isMicTriggeredRef.current = true;
    setUserQuestion("");
    stopPlaceholderRotation();
    start((transcript) => setUserQuestion(transcript));
  }

  function confirmVoice() {
    stop();
  }

  function cancelVoice() {
    stop();
    setUserQuestion("");
    isMicTriggeredRef.current = false;
  }

  return (
    <div className="ai-agent-wrapper">
      <div className={`ai-agent-container ${isOverlayVisible ? "active" : ""}`}>
        {chatMessages.length > 0 && (
          <div className="close-wrapper">
            <div className="close-title">What can I tell you about demand, forecasts, or inventory today?</div>
            <button className="icon-btn" onClick={clearChat} aria-label="Clear chat">
              ✕
            </button>
          </div>
        )}

        {chatMessages.length > 0 && (
          <div className="ai-search-wrapper" ref={chatContainerRef} onScroll={onChatScroll}>
            <div className="chat-container">
              {chatMessages.map((msg, i) => (
                <div className={`chat-message bubble ${msg.sender}`} key={i}>
                  {!msg.isAudio && (
                    <div>
                      <span dangerouslySetInnerHTML={{ __html: msg.text }} />
                      {msg.actions?.length > 0 && (
                        <div className="action-buttons">
                          {msg.actions.map((action, idx) => (
                            <button key={idx} className="chip-btn" onClick={() => sendQuestion(action)}>
                              {action}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {msg.isAudio && <AudioMessage msg={msg} />}
                </div>
              ))}

              {isLoading && (
                <div className="chat-message ai thinking">
                  <div className="bubble thinking-bubble">
                    Copilot is {currentQueryStatus}
                    <div className="shimmer" />
                    <span className="dot" />
                    <span className="dot" />
                    <span className="dot" />
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        <div className="ai-search-container">
          <div className="icon-wrap">
            {!isRecording && <span className="sparkle">✨</span>}
            {isRecording && <canvas ref={canvasRef} width="50" height="50" className="waveform" />}
          </div>

          <div className="ai-search">
            <textarea
              rows={1}
              value={userQuestion}
              onChange={(e) => setUserQuestion(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={PLACEHOLDERS[placeholderIndex]}
              onFocus={() => {
                stopPlaceholderRotation();
                setIsOverlayVisible(true);
              }}
              onBlur={startPlaceholderRotation}
            />
          </div>

          <div className="mic-wrap">
            {!isRecording && (
              <>
                <button className="icon-btn" type="button" onClick={startListening} aria-label="Start voice input">
                  🎙
                </button>
                <button className="icon-btn" type="button" onClick={() => sendQuestion()} aria-label="Send">
                  ➤
                </button>
              </>
            )}
            {isRecording && (
              <>
                <button className="icon-btn" type="button" onClick={confirmVoice} aria-label="Confirm">
                  ✓
                </button>
                <button className="icon-btn" type="button" onClick={cancelVoice} aria-label="Cancel">
                  ✕
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      <div
        className={`agentic-backdrop ${isOverlayVisible ? "active" : ""}`}
        onClick={() => setIsOverlayVisible(false)}
      />
    </div>
  );
}
