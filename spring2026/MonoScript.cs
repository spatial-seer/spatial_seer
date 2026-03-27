using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.Profiling;
using UnityEngine.Networking;

/// <summary>
/// SpyProfiler - Standalone telemetry collector for release builds.
/// Collects memory, CPU/frame timing, and hardware metadata.
/// Pushes data to Supabase as a single row with CSV blob via REST API.
/// Attach to an empty GameObject (e.g., "ProfilerManager").
/// </summary>
public class SpyProfiler : MonoBehaviour
{
    // ─── Configuration ───────────────────────────────────────────────
    [Header("Sampling Configuration")]
    [Tooltip("How often to sample data (in seconds). 1.0 = once per second.")]
    public float sampleInterval = 1.0f;

    [Tooltip("Total duration to collect data (in seconds) before triggering export.")]
    public float collectionDuration = 30.0f;

    [Header("Export")]
    [Tooltip("If true, will attempt to push data to Supabase. If false, logs CSV to console.")]
    public bool pushToSupabase = true;

    [Tooltip("Your Supabase REST endpoint — format: https://<ref>.supabase.co/rest/v1/<table>")]
    private string supabaseUrl = "YOUR_SUPABASE_URL";

    [Tooltip("Your Supabase anon/public JWT key (starts with eyJ...) — from Settings > API in the dashboard")]
    private string supabaseAnonKey = "YOUR_SUPABASE_ANON_KEY";

    // ─── Internal State ──────────────────────────────────────────────
    private List<ProfilerRow> dataRows = new List<ProfilerRow>();
    private float elapsedTime = 0f;
    private int sampleIndex = 0;

    // Frame timing tracking
    private float frameTimeAccumulator = 0f;
    private int frameCount = 0;
    private float worstFrameTime = 0f;
    private float bestFrameTime = float.MaxValue;

    // ─── Data Model (internal only — not sent directly to Supabase) ──
    [Serializable]
    private class ProfilerRow
    {
        public int sample_index;
        public string utc_time;
        public float total_allocated_mb;
        public float total_reserved_mb;
        public float total_unused_mb;
        public float mono_used_mb;
        public float mono_heap_mb;
        public int system_memory_mb;
        public float avg_frame_time_ms;
        public float avg_fps;
        public float worst_frame_ms;
        public float best_frame_ms;
        public int frame_count;
        public string gpu_name;
        public int gpu_memory_mb;
        public string cpu_name;
        public int cpu_cores;
        public int cpu_freq_mhz;
        public string device_model;
        public string device_name;
    }

    // ─── Unity Lifecycle ─────────────────────────────────────────────
    void Start()
    {
        StartCoroutine(CollectionRoutine());
    }

    void Update()
    {
        float dt = Time.unscaledDeltaTime;
        frameTimeAccumulator += dt;
        frameCount++;
        if (dt > worstFrameTime) worstFrameTime = dt;
        if (dt < bestFrameTime) bestFrameTime = dt;
    }

    // ─── Collection Coroutine ────────────────────────────────────────
    IEnumerator CollectionRoutine()
    {
        Debug.Log($"[SpyProfiler] Starting. Duration: {collectionDuration}s, Interval: {sampleInterval}s");

        while (elapsedTime < collectionDuration)
        {
            yield return new WaitForSecondsRealtime(sampleInterval);
            elapsedTime += sampleInterval;
            CollectSample();
        }

        Debug.Log($"[SpyProfiler] Collection complete. {dataRows.Count} samples gathered.");

        if (pushToSupabase && !string.IsNullOrEmpty(supabaseUrl))
        {
            yield return StartCoroutine(PushToSupabase());
        }
        else
        {
            Debug.Log("[SpyProfiler] CSV Output:\n" + BuildCsv());
        }
    }

    // ─── Sample Collection ───────────────────────────────────────────
    void CollectSample()
    {
        float avgFrameTime = (frameCount > 0) ? (frameTimeAccumulator / frameCount) : 0f;
        float avgFps       = (avgFrameTime > 0) ? (1f / avgFrameTime) : 0f;

        var row = new ProfilerRow
        {
            sample_index       = sampleIndex,
            utc_time           = DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss.fff"),

            total_allocated_mb = Profiler.GetTotalAllocatedMemoryLong() / (1024f * 1024f),
            total_reserved_mb  = Profiler.GetTotalReservedMemoryLong()  / (1024f * 1024f),
            total_unused_mb    = Profiler.GetTotalUnusedReservedMemoryLong() / (1024f * 1024f),
            mono_used_mb       = Profiler.GetMonoUsedSizeLong()  / (1024f * 1024f),
            mono_heap_mb       = Profiler.GetMonoHeapSizeLong()  / (1024f * 1024f),
            system_memory_mb   = SystemInfo.systemMemorySize,

            avg_frame_time_ms  = avgFrameTime * 1000f,
            avg_fps            = avgFps,
            worst_frame_ms     = worstFrameTime * 1000f,
            best_frame_ms      = (bestFrameTime < float.MaxValue) ? bestFrameTime * 1000f : 0f,
            frame_count        = frameCount,

            gpu_name           = SystemInfo.graphicsDeviceName,
            gpu_memory_mb      = SystemInfo.graphicsMemorySize,
            cpu_name           = SystemInfo.processorType,
            cpu_cores          = SystemInfo.processorCount,
            cpu_freq_mhz       = SystemInfo.processorFrequency,

            device_model       = SystemInfo.deviceModel,
            device_name        = SystemInfo.deviceName,
        };

        // Reset accumulators
        frameTimeAccumulator = 0f;
        frameCount           = 0;
        worstFrameTime       = 0f;
        bestFrameTime        = float.MaxValue;

        dataRows.Add(row);
        sampleIndex++;
    }

    // ─── Supabase Push (single row: CSV blob + metadata) ─────────────
    IEnumerator PushToSupabase()
    {
        Debug.Log("[SpyProfiler] Pushing data to Supabase...");

        string csv = BuildCsv();

        string json = "{"
            + $"\"csv_data\":\"{SafeStr(csv)}\","
            + $"\"device_model\":\"{SafeStr(SystemInfo.deviceModel)}\","
            + $"\"device_name\":\"{SafeStr(SystemInfo.deviceName)}\","
            + $"\"timestamp\":\"{DateTime.UtcNow:yyyy-MM-ddTHH:mm:ssZ}\""
            + "}";

        Debug.Log($"[SpyProfiler] Payload preview: {json.Substring(0, Mathf.Min(300, json.Length))}...");

        byte[] bodyRaw = Encoding.UTF8.GetBytes(json);

        using (var request = new UnityWebRequest(supabaseUrl, "POST"))
        {
            request.uploadHandler   = new UploadHandlerRaw(bodyRaw);
            request.downloadHandler = new DownloadHandlerBuffer();

            // Required Supabase REST headers
            request.SetRequestHeader("Content-Type",  "application/json");
            request.SetRequestHeader("apikey",        supabaseAnonKey);
            request.SetRequestHeader("Authorization", "Bearer " + supabaseAnonKey);
            request.SetRequestHeader("Prefer",        "return=minimal");

            request.certificateHandler = new BypassCertificate();

            yield return request.SendWebRequest();

            if (request.result == UnityWebRequest.Result.Success)
            {
                Debug.Log($"[SpyProfiler] Success: HTTP {request.responseCode}");
            }
            else
            {
                Debug.LogError($"[SpyProfiler] Failed: {request.error} (HTTP {request.responseCode})");
                Debug.LogError($"[SpyProfiler] Response body: {request.downloadHandler.text}");
            }
        }
    }

    // ─── String Escaping for JSON ────────────────────────────────────
    private string SafeStr(string s) => (s ?? "")
        .Replace("\\", "\\\\")
        .Replace("\"", "\\\"")
        .Replace("\n", "\\n")
        .Replace("\r", "");

    // ─── CSV Builder ─────────────────────────────────────────────────
    string BuildCsv()
    {
        if (dataRows.Count == 0) return "";
        var sb = new StringBuilder();
        sb.AppendLine("sample_index,utc_time,total_allocated_mb,total_reserved_mb,total_unused_mb," +
                      "mono_used_mb,mono_heap_mb,system_memory_mb,avg_frame_time_ms,avg_fps," +
                      "worst_frame_ms,best_frame_ms,frame_count,gpu_name,gpu_memory_mb," +
                      "cpu_name,cpu_cores,cpu_freq_mhz,device_model,device_name");
        foreach (var r in dataRows)
        {
            sb.AppendLine($"{r.sample_index},{r.utc_time},{r.total_allocated_mb:F2},{r.total_reserved_mb:F2}," +
                          $"{r.total_unused_mb:F2},{r.mono_used_mb:F2},{r.mono_heap_mb:F2},{r.system_memory_mb}," +
                          $"{r.avg_frame_time_ms:F2},{r.avg_fps:F1},{r.worst_frame_ms:F2},{r.best_frame_ms:F2}," +
                          $"{r.frame_count},\"{r.gpu_name}\",{r.gpu_memory_mb},\"{r.cpu_name}\"," +
                          $"{r.cpu_cores},{r.cpu_freq_mhz},\"{r.device_model}\",\"{r.device_name}\"");
        }
        return sb.ToString();
    }

    // ─── Cert Bypass (Quest testing only) ────────────────────────────
    private class BypassCertificate : CertificateHandler
    {
        protected override bool ValidateCertificate(byte[] certificateData) => true;
    }
}