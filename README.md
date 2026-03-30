# Callidus: Enterprise AI Call Center Backend

> A highly scalable, unified conversational AI backend bridging PSTN/SIP telephony and WebRTC browser streams through an event-driven Kafka architecture.

---

## 2. Description

Callidus is a production-grade backend orchestration system designed to power the next generation of AI call centers. It unifies traditional phone calls (via SIP/Asterisk) and modern browser-based audio streams (via WebRTC/LiveKit) into a single, cohesive processing pipeline. 

**Why it exists & the problem it solves:**
Historically, building an AI voice agent requires separate monolithic infrastructures for SIP trunking and web audio. This project eliminates that friction by treating all incoming audio as identical `CallRequest` payloads, decoupled from the underlying transport protocol. By using Apache Kafka and a robust routing engine, the system achieves horizontal scalability, intelligent backpressure management, and non-blocking worker allocation.

**Real-world use cases:**
*   **Fully Autonomous AI Receptionists:** AI agents that answer PSTN phone lines, interact natively with customers, and trigger external webhooks securely.
*   **AI Auto-Join Assistants:** AI co-pilots that can dynamically join ongoing human-to-human WebRTC rooms to act in "Assist", "Parallel", or "Takeover" modes.
*   **Intelligent Skill-based Routing:** Calls routing dynamically to AI models or human agent pools based on language matching, caller ID prefixes, time-of-day, and capacity constraints.

---

## 3. Features

*   **Unified Call Pipeline:** Identical downstream behavior for both SIP (PSTN) and WebRTC (Browser) calls.
*   **Kafka-Backed Scheduling & Queue Management:** Durable task delegation guaranteeing exact order, complete with lag-based queue depth monitoring.
*   **Stateless Routing Engine:** Intelligent rule-based evaluation (language, priority, time-of-day, custom metadata) to assign queues, skills, and AI configurations dynamically.
*   **Offline/Overload Fallbacks & Circuit Breakers:** Graceful degradation. The system handles Kafka unavailability by executing local direct AI spawns and issues priority bumps during systemic overload.
*   **Integration Webhooks:** Seamless event propagation to third-party endpoints secured with strict HMAC-SHA256 signatures and TTL-based retry storage.
*   **AI Auto-Join Modes:** Native LiveKit webhook integration to detect human participants and inject AI agents selectively.
*   **Dynamic Load Balancing:** Real-time GPU and VRAM capacity monitoring logic (`pynvml`), allowing intelligent allocation of Whisper/Qwen/Gemini footprints.

---

## 4. Tech Stack

*   **Frontend / Client Support:** 
    * WebRTC (Native Browser APIs)
    * SIP Softphones / PSTN
*   **Backend:** 
    * Python 3
    * FastAPI (REST endpoints, Integration Layer)
    * LiveKit Python SDK (Real-time audio manipulation)
*   **Event Streaming / Queueing:** 
    * Apache Kafka (`aiokafka`)
*   **DevOps / Tools:** 
    * Docker / Docker Compose
    * Asterisk (SIP routing)
*   **Other Integrations:** 
    * AI Core (STT: Whisper, LLM: Gemini/Qwen, TTS: Piper)
    * LiveKit Server (SFU)

---

## 5. Architecture Diagram

[ START: CALL INITIATION ]
      |
      +--------------------------+
      |                          |                         
[ Browser / WebRTC ]       [ PSTN Phone ]          
      |                          |                          
* POST /browser/call/start   * Dial Number            
      |                          |                          
      v                          v                          
+-----------------------------------------------------------+
| FastAPI Backend (API Layer)                               |
| * validate_api_key()                                      |
+-----------------------------------------------------------+
      |                          
      |----> * RE (Routing Engine)                           
      |        * Logic for call distribution              
      register_webhook()
      v                                                     
+----------------------------+               
| Kafka: call_requests       |               
| * KR.produce()             |                
+----------------------------+               
      |
      |----> * SCH (Call Scheduler)
      |        * Monitors GpuCapacity
      v
+----------------------------+
| Kafka: call_assignments    |
| * KA.produce()             |
+----------------------------+
      |
      |----> * WS (AI Worker Service)
      |        * Consumes assignment
      v
+-----------------------------------------------------------+
| AI Task Execution                                         |
| * STT() -> LLM() -> TTS()                                 |
+-----------------------------------------------------------+
      |                          ^
      |                          |
      v                +----------------------------+
[ LiveKit SFU Room ] <|-- * Publishes GpuCapacity   |
      ^                |   * KG (Kafka Topic)       |
      |                +----------------------------+
      |
[ END: Audio Bridged ]


## 6. Project Structure

```text
livekit/
├── main.py                    # FastAPI application entrypoint & bootstrap
├── docker-compose.yml         # Container definitions (Kafka, LiveKit, backend)
├── livekit/                   # Core application package
│   ├── ai_assist/             # AI Auto-Join logic (Manual triggers & Webhooks)
│   │   ├── ai_controller.py   # FastAPI routes for AI joins
│   │   └── ai_join_manager.py # Debounce & session allocation tracker
│   ├── browser/               # Browser-specific endpoints
│   │   └── router.py          # Unified WebRTC call entry and LiveKit token provision
│   ├── integration/           # External API & 3rd-party webhooks
│   │   ├── auth.py            # API key validation logic
│   │   └── service.py         # Webhook dispatch with HMAC-SHA256 & memory safe TTL
│   ├── kafka/                 # Event-driven system core
│   │   ├── config.py          # Connectivity & threshold configurations
│   │   ├── gpu_monitor.py     # pynvml polling for AI model footprint calculation
│   │   ├── producer.py        # Safe ingestion of CallRequests (w/ Fallbacks)
│   │   ├── scheduler.py       # Node assignment & backlog queueing algorithms
│   │   ├── schemas.py         # Pydantic models (CallRequest, GpuCapacity)
│   │   └── worker_service.py  # Consumer delegating requests to asyncio AI tasks
│   ├── routing/               # Intelligent Stateless Router
│   │   ├── engine.py          # Match condition evaluator & human AgentPool mapping
│   │   └── rules.py           # JSON logic parser (Time, Lang, Source, Priority)
│   ├── ai_worker.py           # Main asyncio loop bridging Audio Buffer -> AI Core
│   └── audio_source.py        # PCM resampling (f32 <-> int16) and Track publishing
└── .env                       # Environment secrets and flags
```
