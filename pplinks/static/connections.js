async function refreshConnection() {
  try {
    const response = await fetch('/api/facebook-session');
    if (!response.ok) return;
    const data = await response.json();
    document.getElementById('connection-state').textContent = data.state;
    document.getElementById('connection-message').textContent = data.message || '';
    const busy = ['opening', 'signing_in'].includes(data.state);
    document.querySelectorAll('[data-connection-action]').forEach(button => {
      const action = button.dataset.connectionAction;
      button.disabled = action === 'finish' ? data.state !== 'signing_in' : action === 'cancel' ? !busy : busy;
    });
  } catch (_) { /* The next poll can recover after a local server restart. */ }
}
refreshConnection();
setInterval(refreshConnection, 2000);
