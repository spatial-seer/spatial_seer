using System.Collections;
using System.Collections.Generic;
using UnityEngine;

public class ObjectSpy : MonoBehaviour
{
    [Header("Configuration")]
    public float scanInterval = 1.0f;

    // The list of object names to count
    private string[] targetNames = new string[] {
        "WINDOW_FRAME",
        "TABLE",
        "STORAGE",
        "COUCH",
        "DOOR_FRAME",
        "SCREEN",
        "OTHER",
        "LAMP",
        "WALL_FACE"
    };

    public Dictionary<string, float> currentMetrics = new Dictionary<string, float>();

    void Start()
    {
        // 1. Initialize Spatial Keys
        string[] spatialKeys = {
            "total_volume", "avg_volume",
            "avg_height", "height_variance",
            "avg_wall_proximity", "avg_clustering_distance"
        };

        foreach (var key in spatialKeys)
        {
            currentMetrics[key] = 0f;
        }

        // 2. Initialize Object Count Keys
        foreach (var name in targetNames)
        {
            currentMetrics[$"UO_Name_{name}_count"] = 0f;
        }

        StartCoroutine(ScanRoutine());
    }

    IEnumerator ScanRoutine()
    {
        while (true)
        {
            ScanSpatialData();
            yield return new WaitForSeconds(scanInterval);
        }
    }

    void ScanSpatialData()
    {
        MeshRenderer[] allRenderers = FindObjectsByType<MeshRenderer>(FindObjectsSortMode.None);
        if (allRenderers.Length == 0) return;

        // 1. Reset object counts to 0 for this specific scan
        foreach (var name in targetNames)
        {
            currentMetrics[$"UO_Name_{name}_count"] = 0f;
        }

        List<Vector3> wallPositions = new List<Vector3>();
        List<Vector3> furniturePositions = new List<Vector3>();

        float totalVolume = 0f;
        float sumHeight = 0f;

        // ---------------------------------------------------------
        // PASS 1: Categorize, Volume, Height, AND Object Counting
        // ---------------------------------------------------------
        foreach (var renderer in allRenderers)
        {
            if (renderer == null || renderer.gameObject == null) continue;

            string objName = renderer.gameObject.name;

            // --- OBJECT COUNTING LOGIC ---
            foreach (var target in targetNames)
            {
                if (objName.Contains(target))
                {
                    currentMetrics[$"UO_Name_{target}_count"]++;
                }
            }

            // --- SPATIAL MATH LOGIC ---
            // Separate Walls from Furniture
            if (objName.Contains("WALL_FACE"))
            {
                wallPositions.Add(renderer.transform.position);
            }
            else
            {
                // It is furniture/objects
                furniturePositions.Add(renderer.transform.position);

                // Volume / Bounding Boxes
                Vector3 size = renderer.bounds.size;
                totalVolume += (size.x * size.y * size.z);

                // Height Distribution
                sumHeight += renderer.transform.position.y;
            }
        }

        int furnCount = Mathf.Max(1, furniturePositions.Count); // Prevent divide by zero
        float avgHeight = sumHeight / furnCount;

        // Height Variance
        float sumSquaredDiffs = 0f;
        foreach (var pos in furniturePositions)
        {
            float diff = pos.y - avgHeight;
            sumSquaredDiffs += (diff * diff);
        }
        float heightVariance = sumSquaredDiffs / furnCount;

        // ---------------------------------------------------------
        // FEATURE: Wall Proximity
        // ---------------------------------------------------------
        float totalWallDist = 0f;
        if (wallPositions.Count > 0)
        {
            foreach (var fPos in furniturePositions)
            {
                float minDist = float.MaxValue;
                foreach (var wPos in wallPositions)
                {
                    float dist = Vector3.Distance(fPos, wPos);
                    if (dist < minDist) minDist = dist;
                }
                totalWallDist += minDist;
            }
        }
        float avgWallProximity = (wallPositions.Count > 0) ? (totalWallDist / furnCount) : 0f;

        // ---------------------------------------------------------
        // FEATURE: Clustering & Distance
        // ---------------------------------------------------------
        float totalClusterDist = 0f;
        int pairCount = 0;
        int limit = Mathf.Min(furniturePositions.Count, 100);

        for (int i = 0; i < limit; i++)
        {
            for (int j = i + 1; j < limit; j++)
            {
                totalClusterDist += Vector3.Distance(furniturePositions[i], furniturePositions[j]);
                pairCount++;
            }
        }
        float avgClusteringDistance = (pairCount > 0) ? (totalClusterDist / pairCount) : 0f;

        // ---------------------------------------------------------
        // SAVE METRICS
        // ---------------------------------------------------------
        currentMetrics["total_volume"] = totalVolume;
        currentMetrics["avg_volume"] = totalVolume / furnCount;
        currentMetrics["avg_height"] = avgHeight;
        currentMetrics["height_variance"] = heightVariance;
        currentMetrics["avg_wall_proximity"] = avgWallProximity;
        currentMetrics["avg_clustering_distance"] = avgClusteringDistance;
    }
}