# XR Defense

This repository hosts the BURGS Project "Spatial Seer: Exploiting Telemetry to Expose XR User Environment". This repository hosts the team's work to investigate how performance metrics can be exploited to expose an XR user's location type. The project utilizes the Magic Leap 2 and Meta Quest 3. We used Unity to develop our entrypoint for our cyber attack. 

Author(s): 
Allie Craddock (alliec45@vt.edu) | 
Gayatri Kamtala (gayatrikam@vt.edu) |
Casie Peng (casiepeng@vt.edu) | 
Claire Shin (cshinh@vt.edu)

# Repository Structure 
- [`/classifier_models`](https://github.com/alliec45/mixed_reality_defense/tree/main/classifier_models): Contains all of our datasets, data preprocessing, and data analysis along with our SVM models. 
    - [`/magic_leap_2`](https://github.com/alliec45/mixed_reality_defense/tree/main/classifier_models/magic_leap_2): Contains all datasets and analysis for the Magic Leap 2 headset.
        - [`/power_profiler`](https://github.com/alliec45/mixed_reality_defense/tree/main/classifier_models/magic_leap_2/power_profiler)
    - [`/meta_quest_3`](https://github.com/alliec45/mixed_reality_defense/tree/main/classifier_models/meta_quest_3): Contains all datasets and analysis for the Meta Quest 3 headset across multiple performance profilers. 
        - [`/ovr_metrics`](https://github.com/alliec45/mixed_reality_defense/tree/main/classifier_models/meta_quest_3/ovr_metrics): Contains all the datasetsa and analysis for the scans which are profiled with the OVRMetrics tool. 
- [`/video_captures`](https://github.com/alliec45/mixed_reality_defense/tree/main/video_captures): Contains all videography and photography from the headset. 
- [`/archive`](https://github.com/alliec45/mixed_reality_defense/tree/main/archive): Contains weekly updates from the team since the beginning of the project, divided by year. Also includes archived tar files from out-of-date data collection and analysis. 

# Literature Review 
## MR Location Detection Research Papers:
- [It's All in Your Headset](https://www.usenix.org/system/files/sec23fall-prepub-131-zhang-yicheng.pdf)
    - Purpose - Find side-channel attacks through hand movements, concurrent applications, and location detection. 
    - Programs - Python, C#, Memory allocation API (AppMemoryUsage), CPU, GPU, Vertex Count, Game thread time, Render thread time, backgroundTaskHost
    - Technology - Hololens, MetaQuest2
- [Apple Vision Pro’s Eye Tracking Exposed What People Type](https://nam04.safelinks.protection.outlook.com/?url=https%3A%2F%2Fwww.wired.com%2Fstory%2Fapple-vision-pro-persona-eye-tracking-spy-typing%2F&data=05%7C02%7Ccasiepeng%40vt.edu%7C3f171c6378b241fd2df408dcd382f7ba%7C6095688410ad40fa863d4f32c1e3a37a%7C0%7C0%7C638617806691080948%7CUnknown%7CTWFpbGZsb3d8eyJWIjoiMC4wLjAwMDAiLCJQIjoiV2luMzIiLCJBTiI6Ik1haWwiLCJXVCI6Mn0%3D%7C0%7C%7C%7C&sdata=XhRvlu5DaztAClu0slOXyrVsUOf8wvRaxJPwVpEvSAI%3D&reserved=0)
    - Purpose - informed of the accuracy of attacks on user's keyboard inputs from eye tracking in Apple Vision Pro
    - Technology - Apple Vision Pro 
- [Inferring Semantic Location from Spatial Maps in Mixed Reality](https://habiba-farrukh.github.io/files/LocIn.pdf)
    - Purpose - Create a framework which is able to predict locations for location-detection attacks (LocIn)
    - Programs - Microsoft MRTK's Spatial Mapping 
    - Technology - HoloLens 2
- [OVR seen: Auditing Network Traffic and Privacy Policies in Oculus VR](https://www.usenix.org/system/files/sec22-trimananda.pdf)
    - Purpose - To demonstrate how cyber attackers can get access to application information from a VR headset through network traffic. 
    - Programs - OVRSeen, PolicyLint, PoliCheck, and Polisis. AntMonitor, Frida client, standard Android library, the Mbed TLS library provided by the Unity SDK, and the Unreal version of the OpenSSL library. Here's a github of their programs (which gave them access to applications): https://github.com/UCI-Networking-Group/OVRseen 
    - Technology - Oculus, Quest 2
- [Towards Privacy-Preserving Mixed Reality: Legal and Technical Implications](https://drive.google.com/file/d/1UQjyLQMPWSMqWTOcaGYq9C0GImGY7Kvu/view?usp=sharing)
    - Purpose - Analyze how much MR headsets can collect personal and environmental data, and the implications of the collection without consent 
    - Technology - MR headsets

## Workshop Papers:
- [Understanding the long-term impact and perceptions of privacy-enhancing technologies for bystander obscuration in AR](https://ieeexplore.ieee.org/abstract/document/10765311)
- [Deceptive Patterns and Perceptual Risks in an Eye-Tracked Virtual Reality](http://www.leelisle.com/wp-content/uploads/2024/03/Deceptive_Patterns.pdf)

## Tutorials: 
- [Magic Leap 2 Tools & Unity - Tutorial (Video)](https://www.youtube.com/watch?v=KqH0zv3e2AY)
    - Purpose - Setup ML2, tools to interact with ML2 and setting up the Hub with Unity Hub. 
    - Programs - Unity
    - Technology - ML2  
- [Device Stream from Magic Leap Hub 3](https://www.magicleap.care/hc/en-us/articles/6589955346957-Device-Stream)
    - Purpose: how to connect the AR headset to your device to stream and share files. 
- [How to Save Meshing Samples With ML2](https://forum.magicleap.cloud/t/how-to-save-meshes-from-ml2-meshing-sample-or-the-spaces-app/4040/4?u=alliec45)
    - Purpose: how to save mesh files from ML2 to to the computer for later studying. Compare mesh sample size with performance indicators. 
- [Magic Leap Development: Adding Spatial Mapper, Placement Feature, and Controller](https://www.youtube.com/watch?v=Ols3g_BHv1I)
    - Purpose: how to set up a Unity program that will take a mesh scan of the surroundings. 
- [Magic Leap Development: Simple Meshing](https://developer-docs.magicleap.cloud/docs/guides/unity/perception/meshing/unity-simple-meshing/)
    - Purpose: set up a simple meshing in Unity

## API/Library Documentation:
- [Magic Leap 2 Hub 3](https://developer-docs.magicleap.cloud/docs/guides/developer-tools/ml-hub-3/get-started/)
- [Magic Leap 2 Unity OpenXR](https://developer-docs.magicleap.cloud/docs/category/unity-openxr/)
- [Unity FrameTimeManager API](https://unity.com/blog/engine-platform/detecting-performance-bottlenecks-with-unity-frame-timing-manager)
- [Power Profiler Package](https://developer-docs.magicleap.cloud/docs/device/power/power-profiler/#)
- [Radeon GPU Profiler Package](https://developer-docs.magicleap.cloud/docs/guides/developer-tools/lumin-aosp-tools/radeon-gpu-profiler/)
- [Pandas Library](https://pandas.pydata.org/docs/)
- [Matplotlib Library](https://matplotlib.org/stable/index.html)

# Future Goals: 
1. Develop event-based API to automatically export data 
2. Cross-validate findings for Mixed Reality and Virtual Reality programs
3. Establish more room types for our machine learning models
