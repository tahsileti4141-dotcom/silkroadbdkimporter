# 🧩 Game File Importer V11 FIXED (BMS/BSK/BMT) BLENDER

**Blender Add-on** for importing *Silkroad Online / JMXV*-based game assets — including **meshes (.bms)**, **skeletons (.bsk)**, and **materials (.bmt + .ddj)** — directly into Blender.

> ✅ **Version:** 4.5.1  
> 🧠 **Developed by:** Sizinle Geliştirildi - FIXED  
> 🧰 **Blender Version:** 2.80+  
> 🎨 **Category:** Import-Export

---

## 🌟 Features

- **BMS Importer:** Loads game mesh data with correct vertex, UV, and skin weight mapping.  
- **BSK Importer:** Imports skeletons with correct bone hierarchy and rotation fix for Blender.  
- **BMT + DDJ Support:** Automatically applies materials and converts `.ddj` textures to `.png` using Pillow.  
- **Armature Binding:** Auto-binds meshes to skeletons with smart fallback for weight mapping.  
- **Split Skeletons:** Optional “split chain” mode to separate skeletons into multiple armatures.  
- **Coordinate Conversion:** Converts Silkroad’s coordinate system (Y→-Z, Z→Y) to Blender’s Z-up system.  
- **Auto Scaling & Alignment:** Matches skeleton and mesh scales and positions automatically.  
- **Debug Output:** Detailed console logs for troubleshooting complex rigs and materials.

---

## 📦 Installation

1. Download the repository or the `.zip` release.  
2. Open **Blender → Preferences → Add-ons → Install…**  
3. Select the downloaded ZIP file.  
4. Enable **Game File Importer V11 FIXED (BMS/BSK/BMT)** in the add-on list.  
5. Find it under:  
   `View3D → Sidebar (N) → Game Import`  

---

## 🧱 Supported Formats

| Format | Description | Required |
|:-------|:-------------|:---------:|
| `.bms` | Mesh file (geometry + skin) | ✅ |
| `.bsk` | Skeleton file (bones + hierarchy) | ⚙️ Optional |
| `.bmt` | Material file (links to DDJ textures) | ⚙️ Optional |
| `.ddj` | Texture file (auto-converted to PNG) | ⚙️ Optional |

---

## 🪄 Usage Guide

### 1️⃣ Load Files
- Add **BMS** files (mesh models).  
- Optionally set a **BSK** file (skeleton).  
- Optionally set a **BMT** file (materials).  
- Add **DDJ** textures if you want them auto-converted and applied.

### 2️⃣ Settings
- ✅ `Combine Meshes` — merges multiple BMS files into one mesh.  
- ✅ `Bind Mesh` — automatically attaches mesh to skeleton.  
- ✅ `Auto DDJ` — converts `.ddj` to `.png` automatically (requires Pillow).  
- ✅ `Split skeleton chains` — creates multiple armatures for complex rigs.

### 3️⃣ Import
Click **Import Game Files** to process everything.  
You’ll see debug logs in the **Blender Console** (toggle with `Window → Toggle System Console`).

---

## 🧰 Requirements

- Blender **2.80** or newer  
- Python **3.7+** (bundled with Blender)  
- Pillow library (optional, for `.ddj` → `.png` conversion)

To install Pillow manually:
```bash
pip install pillow
