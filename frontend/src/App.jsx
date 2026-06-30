import { useState } from 'react';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function App() {
  const [prompt, setPrompt] = useState('Create a technical document for a multi-agent architecture with deployment guidance.');
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (event) => {
    event.preventDefault();
    setLoading(true);
    setError('');
    setResult('');

    try {
      const response = await fetch(`${API_BASE_URL}/invoke`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ prompt }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail?.[0]?.msg || data.message || 'Request failed');
      }

      setResult(data.message || 'Completed without a response.');
    } catch (err) {
      setError(err.message || 'Something went wrong');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="app-shell">
      <section className="card">
        <div className="card-header">
          <p className="eyebrow">Opoagent</p>
          <h1>Ask the agent to draft documentation</h1>
          <p className="subtitle">
            Send a prompt to the FastAPI backend and view the generated response here.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="prompt-form">
          <label htmlFor="prompt">Prompt</label>
          <textarea
            id="prompt"
            rows="6"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Describe what you want the agent to create"
          />
          <button type="submit" disabled={loading}>
            {loading ? 'Running...' : 'Run Agent'}
          </button>
        </form>

        {error ? <div className="message error">{error}</div> : null}

        {result ? (
          <div className="message result">
            <h2>Response</h2>
            <pre>{result}</pre>
          </div>
        ) : null}
      </section>
    </main>
  );
}

export default App;
