import api from "./http";

// ---------------------------------------------------------------------------
// Background job routes.
//
// Certificate operations (EasyRSA) used to run inside the HTTP request, which
// blew past the browser timeout on slow machines and produced "false failures":
// the frontend reported an error while the backend had actually succeeded.
// The collection/action endpoints now answer 202 + job_id straight away and the
// worker keeps going, so the frontend polls GET /jobs/{job_id} for the result.
//
// Keep the trailing slash on the collection route (GET /jobs/): FastAPI declares
// it as @router.get("/"), and calling it without the slash answers a 307
// redirect. The item route /jobs/{job_id} is declared without one.
// ---------------------------------------------------------------------------

export const getJob = (jobId) => api.get(`/jobs/${jobId}`);

export const listJobs = (limit) => api.get("/jobs/", { params: { limit } });

// Job statuses reported by the backend.
export const JOB_SUCCEEDED = "succeeded";
export const JOB_FAILED = "failed";

// Message used when the job id is no longer known to the backend (404): results
// are only kept for ~1 hour, so this is terminal - retrying cannot help.
export const JOB_GONE_MESSAGE = "任务不存在或已过期";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Poll a background job until it finishes.
 *
 * Resolves with the full job object once `status === "succeeded"` - callers read
 * the payload from `job.result`. Rejects with an Error when the job fails, when
 * the job id is unknown/expired (404) or when `timeoutMs` elapses.
 *
 * Requests are awaited one at a time (no overlapping polls): the next request is
 * only scheduled after the previous one has settled, so a slow backend cannot
 * pile up in-flight requests.
 */
export const waitForJob = async (jobId, { timeoutMs = 300000, intervalMs = 1000, onTick } = {}) => {
  if (!jobId) {
    throw new Error("缺少任务编号，无法获取执行结果");
  }
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    let job;
    try {
      const { data } = await getJob(jobId);
      job = data;
    } catch (e) {
      if (e?.status === 404) {
        // Terminal: the job id is unknown or its result has already been
        // cleaned up. Retrying would only spin until the timeout.
        const gone = new Error(JOB_GONE_MESSAGE);
        gone.status = 404;
        throw gone;
      }
      throw e;
    }
    if (typeof onTick === "function") {
      onTick(job);
    }
    if (job?.status === JOB_SUCCEEDED) {
      return job;
    }
    if (job?.status === JOB_FAILED) {
      throw new Error(job.error || "任务执行失败");
    }
    // Never sleep past the deadline, so a slow response cannot make us wait
    // longer than the caller asked for.
    if (Date.now() >= deadline) {
      throw new Error("操作仍在后台执行，可稍后刷新页面查看结果");
    }
    await sleep(Math.min(intervalMs, Math.max(0, deadline - Date.now())));
  }
};

export default { getJob, listJobs, waitForJob };
