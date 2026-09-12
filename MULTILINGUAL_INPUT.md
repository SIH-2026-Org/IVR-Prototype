# Multilingual input and bounded IVR demo

The architecture uses Twilio DTMF Gather for fixed answers and Twilio Record for
open answers. FastAPI securely downloads each Twilio recording, converts it with
ffmpeg to PCM16 mono 16 kHz WAV, calls Bhashini ASR, then sends only the returned
text and CallSid profile to Node `/api/v1/match`. Sarvam remains Node's optional
semantic extractor; the deterministic engine remains the eligibility authority.

## Start locally

Terminal 1:
```bash
cd "$HOME/Documents/SIH'26/Rule-Engine"
npm install
npm run dev
```
Terminal 2:
```bash
cd "$HOME/Documents/SIH'26/saarthi-setu-ivr"
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```
Terminal 3:
```bash
ngrok http 8000
```
Copy ngrok's HTTPS URL into the IVR `.env` as `PUBLIC_BASE_URL`, restart FastAPI,
and set the Twilio incoming voice webhook to `https://YOUR-DOMAIN/ivr` (POST).
Use a single FastAPI worker because sessions are in memory.

## Configuration

Node `.env` (keep existing Meta configuration):
```dotenv
PORT=3000
SEMANTIC_PROVIDER=sarvam
SARVAM_ENABLED=true
SARVAM_API_KEY=
SARVAM_CHAT_MODEL=sarvam-105b
SARVAM_TIMEOUT_MS=3500
INPUT_DEBUG=true
```
Enter your key only in `.env`. `SEMANTIC_PROVIDER=local` or
`SARVAM_ENABLED=false` disables AI. Missing keys, provider errors, rate limits,
timeouts and schema errors retain deterministic extraction and log a sanitized
`SARVAM FALLBACK` reason. No paid Sarvam invocation was used for automated tests.
The existing WhatsApp translation service still uses its separate `SARVAM_API`
setting; this change does not route IVR understanding through that translator.

IVR `.env`:
```dotenv
PUBLIC_BASE_URL=https://YOUR-DOMAIN.ngrok-free.app
DRE_URL=http://localhost:3000/api/v1/match
SPEECH_PROVIDER=bhashini
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
BHASHINI_ENABLED=true
BHASHINI_UDYAT_KEY=
BHASHINI_INFERENCE_KEY=
BHASHINI_ASR_SERVICE_ID=
IVR_TARGET_MAX_CALLBACKS=9
IVR_DEBUG=true
SESSION_TTL_SECONDS=1200
MAX_CONVERSATION_TURNS=15
MAX_FIELD_RETRIES=2
LOW_STT_CONFIDENCE=0.35
PINCODE_API_URL=https://api.postalpincode.in/pincode/{pincode}
```
Leave `PINCODE_API_URL` empty to disable lookup and fall back to spoken state.
The existing PIN resolver is unchanged. Never place real keys in `.env.example`.

## Understanding contract

`dre_client` adds `original_text`, `current_fields` (zero to two canonical names),
and `confirmed_fields` alongside the existing text/profile request. Node returns
the existing `normalized_profile`, `profile_summary`, ranking and `missing_fields`,
plus `input_understanding`. Existing text-only and profile-only requests work.

Node preserves the original utterance, performs a small local normalization and
deterministic extraction, then optionally invokes Sarvam. Short single-field
amount answers acquire only the requested financial context. Full sentences
retain their own semantics. Hinglish `dedh`, `dhai`, household income and explicit
business-starting phrases have local support.

Sarvam is requested for native-script/code-mixed input, weak extraction, missing
requested fields, corrections or multiple clauses. Simple `My age is 28`, repeat,
help and recognizable question intents need no AI call. The only new external
endpoint is `POST https://api.sarvam.ai/v1/chat/completions`; it receives the
utterance, language hint, requested fields and canonical confirmed-field context.
It never receives CallSid, phone number, scheme definitions or credentials in
the prompt. Authentication is in the API header.

The strict JSON schema has `intent`, typed nullable `fields`, `explicit_fields`,
`corrections`, `uncertain_fields`, per-field verbatim `evidence`, `user_question`,
`language_code` and `normalized_text`. Additional keys, invalid enums and wrong
types are rejected locally as well as constrained through `response_format`.
Model prose is never accepted. The model cannot select schemes or change rules.

Only explicit, certain, evidence-backed AI values are considered. Confirmed
values are protected except for explicit corrections. Deterministic/AI conflicts
retain the deterministic candidate and mark uncertainty. Unknown/null values do
not erase session values. Activity additionally needs a recognized activity term
in the original evidence; currently Hindi डेयरी/दुग्ध and सिलाई are grounded too.
This deliberately rejects unsupported native activity names rather than guessing.
Income never becomes project cost, and loan never supplies project cost.

## Conversation and callback planning

The existing TTL store retains profile, confirmed fields, current field(s), raw
conversation, retries and callback count. The three opening routes are counted.
The planner filters DRE missing fields against known values. When the number of
missing fields approaches remaining callbacks, it collects either income plus
project cost, or activity plus project cost, in one speech turn. It never requests
more than two at once. Callers should name each amount in a batched answer.

Fixed answers use DTMF: gender (F/M/O), existing business (true/false), area type
(rural/urban), social category (SC/ST/OBC/EWS/GEN/MINORITY). DTMF answers are marked
confirmed. Repeat/help/questions repeat the current question with a short
centralized English/Hindi explanation; they do not invoke eligibility reasoning.
General questions are not answered by an unrestricted chatbot.

Confidence below 0.35 re-asks without parsing. A changed numeric follow-up with
confidence from 0.35 to below 0.65, reported extraction uncertainty, or an explicit
confirmation request uses the existing DTMF confirmation. Clear numbers need no
extra callback. Initial multi-fact values do not each consume confirmation turns.
Retries remain capped at two. On the final available turn the planner can collect
a compatible pair; if required information or confirmation is still incomplete
at the configured budget, it explains the limit and ends without guessing.
This is a demo budget, not an assertion about Twilio account-specific limits.

## Debugging and validation

`INPUT_DEBUG=true` prints Node understanding: raw text, language hint/detection,
normalized text, deterministic/AI fields, merged fields, uncertainty, fallback and
semantic duration. `IVR_DEBUG=true` prints the final returned eligible schemes,
names, scores and available EMI; only `eligibility.status == ELIGIBLE` is used,
maximum three. No result is manufactured when that set is empty. The same filter
applies to spoken output. Final scheme narration is currently English.

Timing separates Node semantic duration, DRE duration and Python elapsed turn
time. Gather STT duration is explicitly unavailable; it is not invented. Disable
debug in production: profiles and raw utterances are personal information. Audio
is not persisted, and sessions are deleted on successful completion or terminal
failure. Legacy IVR/DRE turn logging predates the new debug switches.

Run tests:
```bash
cd "$HOME/Documents/SIH'26/Rule-Engine"
npm test
cd "$HOME/Documents/SIH'26/saarthi-setu-ivr"
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/simulate_multilingual.py
```
The simulation uses the real local Node API, with no Twilio call. Start Node with
`SEMANTIC_PROVIDER=local SARVAM_ENABLED=false npm start` for deterministic replay.
Native-Hindi Sarvam tests mock the provider. Live Sarvam availability, latency and
model quality need a separate credentialed test.

## Verified local simulated call

After three opening callbacks:
1. Caller: “main Rajasthan se hoon aur dairy ka kaam shuru karna hai. My age is 28.”
2. IVR asks annual family income and total project cost together.
3. Caller: “Meri annual family income one lakh twenty thousand hai aur project cost do lakh hai.”
4. IVR asks social category. Caller presses 1 (SC).
5. IVR asks gender. Caller presses 1 (female).
6. DRE returns OK. Total callbacks including opening: **7**.

Actual local terminal result:
```text
FINAL SCHEME MATCH
STATUS: OK
activity: dairy
income_annual: 120000
project_cost: 200000
existing_business: False
1. Udyogini Scheme for Women Empowerment
   Eligibility: ELIGIBLE
   Score: 99
   Estimated EMI: 2905
2. Kisan Credit Card Scheme
   Eligibility: ELIGIBLE
   Score: 97
   Estimated EMI: 3683
3. Pradhan Mantri Samajik Utthan evam Rozgar Adharit Jankalyan (PM-SURAJ)
   Eligibility: ELIGIBLE
   Score: 87
   Estimated EMI: 2706
```
These are repository-engine outputs for this test profile, not government claims
or hardcoded recommendations. Scheme rules, ranking and financial formulas were
not changed by this multilingual task.

## Manual phone test and future adapters

From your verified number, call Twilio → press 2 for English → press 1 for matching.
Say “I am 28 years old from Rajasthan and want to start a dairy business.”
Answer the combined income/cost question with both labels, then SC (1) and female
(1) when asked. Expect continued Gathers, no routine numeric confirmations, then
actual ranked recommendations and a `FINAL SCHEME MATCH` terminal block.
Repeat with the simulated Hinglish sentence. No paid phone call was initiated by
the implementation or tests.

`speech_provider.AudioSpeechProvider` is implemented by `BhashiniASRProvider` for
recorded audio. Bhashini uses the proven Dhruva inference endpoint and request
shape (`taskType=asr`, `audioFormat=wav`, `samplingRate=16000`). A future real-time
adapter can use Twilio Media Streams without changing conversation or DRE logic.
If download, ffmpeg, or Bhashini fails, the response re-asks the same question once
using Twilio Gather STT; it never pretends that a recording contains SpeechResult.

Limits: short recordings are turn-based rather than streaming; grounded native
activities still depend on Sarvam when deterministic vocabulary is insufficient;
no unrestricted document Q&A; in-memory
single-process sessions; more retries/PIN failures may exhaust the nine-callback
budget. The callback count is an application count, not a Twilio platform quota
measurement. Production should add transport webhook authentication and shared
session storage as separate work.
