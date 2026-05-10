// CUI // SP-CTI
// React hook: Bedrock Inline Agent for AI-powered UX
// Drop into your React app and replace manual API calls.
import { useState, useCallback } from 'react';

const INVOKE_URL = process.env.REACT_APP_BEDROCK_AGENT_ENDPOINT || '/api/bedrock-agent';

export function useBedrockAgent(agentId, agentAliasId) {
  const [response, setResponse] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [sessionId] = useState(() => crypto.randomUUID());

  const invoke = useCallback(async (inputText) => {
    setLoading(true);
    setError(null);
    setResponse('');
    try {
      const res = await fetch(INVOKE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agentId, agentAliasId, sessionId, inputText }),
      });
      if (!res.ok) throw new Error(`Agent error: ${res.status}`);
      // Stream the response
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let result = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value);
        result += chunk;
        setResponse(result);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [agentId, agentAliasId, sessionId]);

  return { invoke, response, loading, error };
}
