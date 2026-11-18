async function updateJobs() {
  const rows = document.querySelectorAll('[data-job]');
  if (!rows.length) return;
  try {
    const response = await fetch('/api/jobs');
    if (!response.ok) return;
    const jobs = await response.json();
    for (const job of jobs) {
      const row = document.querySelector(`[data-job="${job.id}"]`);
      if (!row) continue;
      for (const key of ['status', 'total', 'new', 'existing', 'updated', 'failed']) {
        const element = row.querySelector(`[data-field="${key}"]`);
        if (element) element.textContent = job[key];
      }
      row.querySelector('[data-field="progress"]').textContent = job.work_total ? `${job.processed} / ${job.work_total}` : '—';
      const progress = row.querySelector('progress');
      if (progress) { progress.value = job.processed; progress.max = job.work_total || 1; }
    }
  } catch (_) { /* Keep existing state visible during a temporary disconnect. */ }
}
setInterval(updateJobs, 2500);
document.getElementById('load-preview')?.addEventListener('click', function () {
  const frame = document.getElementById('post-preview');
  frame.src = frame.dataset.previewSrc;
  frame.hidden = false;
  this.hidden = true;
});
