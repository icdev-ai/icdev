# Prompt Anatomy

A prompt is not just text — it's a structured instruction that shapes every aspect of an LLM's output. Understanding prompt anatomy is the difference between an AI that almost works and one that ships to production.

## The four components of a production prompt

### 1. System Prompt
Sets the model's persona, constraints, and output format. This runs on every call and is the most expensive part (always counts against your token budget).

```
You are an expert GovCon proposal analyst. You respond ONLY in structured JSON.
Never include preamble. Confidence scores must be 0.0–1.0.
```

### 2. Context / Retrieved Content
Background information injected from your data sources — documents, database results, tool outputs. This is where RAG content lands.

```
CONTEXT:
[Document 1]: FedRAMP Authorization guidance, NIST SP 800-53 Rev 5...
[Document 2]: Agency RFP requirements, Section L.4.2...
```

### 3. User Message
The actual instruction for this specific call. Keep it precise.

```
USER: Identify the top 3 compliance gaps in the draft System Security Plan
against the FedRAMP High baseline. Return JSON with gap, severity, and recommendation.
```

### 4. Output Format Specification
Tell the model exactly what format you want. Ambiguous format = inconsistent output = broken parsing downstream.

```
Return ONLY valid JSON array: [{"gap": "...", "severity": "high|medium|low", "recommendation": "..."}]
```

## Your task

Complete the `build_prompt()` function that assembles a structured prompt from its components, then call `simulate_llm_call()` to test it.
