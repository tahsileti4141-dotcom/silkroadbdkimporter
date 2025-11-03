bl_info = {
    "name": "Game File Importer V11 FIXED (BMS/BSK/BMT)",
    "author": "Sizinle Geliştirildi - FIXED",
    "version": (4, 5, 1),
    "blender": (2, 80, 0),
    "location": "View3D > Sidebar > Game Import",
    "description": "DEBUG: ROC modeli için texture debug output eklendi.",
    "category": "Import-Export",
}

import bpy
import struct
import os
import traceback
import math
from bpy.props import StringProperty, PointerProperty, CollectionProperty, IntProperty, BoolProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList
from mathutils import Vector, Quaternion, Matrix

try:
    from PIL import Image
    import io
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ============================================================================
# SRO→Blender Koordinat Sistemi Dönüşüm Yardımcıları
# ============================================================================
SRO_TO_BLENDER_POS_MATRIX = Matrix((
    (1,  0,  0, 0),    # X -> X
    (0,  0, -1, 0),    # Y -> -Z (90° X rotation for OBJ compatibility)
    (0,  1,  0, 0),    # Z -> Y
    (0, 0, 0, 1)
))
C3 = SRO_TO_BLENDER_POS_MATRIX.to_3x3()

def convert_vec_sro_to_blender(v: Vector) -> Vector:
    """SRO vektörünü Blender koordinat sistemine dönüştür"""
    return SRO_TO_BLENDER_POS_MATRIX @ v

def convert_quat_sro_to_blender(q: Quaternion) -> Quaternion:
    """SRO quaternion'ını Blender koordinat sistemine dönüştür (temel değişimi/benzerlik dönüşümü)"""
    # q: (w, x, y, z) biçiminde, SRO kemik yerel uzayında
    m = q.to_matrix()               # 3x3
    m_conv = C3 @ m @ C3.inverted() # temel değişimi (benzerlik)
    return m_conv.to_quaternion().normalized()

# ============================================================================
# Property Groups & UI Sınıfları
# ============================================================================
class FileListItem(PropertyGroup):
    path: StringProperty(name="File Path", subtype='FILE_PATH')

class GameImporterSettings(PropertyGroup):
    bms_files: CollectionProperty(type=FileListItem, name="BMS Files")
    active_bms_index: IntProperty(default=0)
    ddj_files: CollectionProperty(type=FileListItem, name="DDJ Files")
    active_ddj_index: IntProperty(default=0)
    bmt_file: StringProperty(name="BMT File", subtype='FILE_PATH')
    bsk_file: StringProperty(name="BSK File", subtype='FILE_PATH')
    combine_meshes: BoolProperty(name="Combine Meshes", default=True)
    apply_materials: BoolProperty(name="Apply Materials", default=True)
    auto_convert_ddj: BoolProperty(name="Auto Convert DDJ", default=True)
    import_skeleton: BoolProperty(name="Import Skeleton", default=True)
    bind_mesh: BoolProperty(name="Bind Mesh to Skeleton", default=True)
    split_armatures: BoolProperty(name="Split skeleton chains into separate armatures", default=False)
    split_root_children: BoolProperty(name="If single root, split by its children", default=True)

# ============================================================================
# Main Importer
# ============================================================================
class GameImporter(Operator):
    bl_idname = "import_scene.game_files"
    bl_label = "Import Game Files"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        settings = context.scene.game_importer_settings
        try:
            created_armature = None
            created_armatures = []
            created_meshes = []
            all_mesh_data = []
            
            # STEP 1: Import Skeleton (BSK)
            if settings.import_skeleton and settings.bsk_file and os.path.exists(settings.bsk_file):
                self.report({'INFO'}, "Importing skeleton...")
                result = self.import_bsk(context, settings.bsk_file, settings.split_armatures)
                # import_bsk may return a single armature or a list if split is enabled
                if isinstance(result, list):
                    created_armatures = result
                    created_armature = created_armatures[0] if created_armatures else None
                    if created_armatures:
                        self.report({'INFO'}, f"✓ Skeletons: {len(created_armatures)} objects created")
                else:
                    created_armature = result
                    if created_armature:
                        self.report({'INFO'}, f"✓ Skeleton: {created_armature.name}")
            
            # STEP 2: Import Meshes (BMS)
            if len(settings.bms_files) > 0:
                self.report({'INFO'}, "Importing meshes...")
                for item in settings.bms_files:
                    if item.path and os.path.exists(item.path):
                        mesh_data = self.import_bms(item.path)
                        if mesh_data:
                            all_mesh_data.append(mesh_data)
                
                if all_mesh_data:
                    if settings.combine_meshes:
                        name = all_mesh_data[0].get('name', 'CombinedMesh')
                        obj = self.create_combined_mesh(context, all_mesh_data, name)
                        created_meshes.append(obj)
                        self.report({'INFO'}, f"✓ Combined mesh '{name}' created")
                    else:
                        for mesh_data in all_mesh_data:
                            obj = self.create_mesh_object(context, mesh_data)
                            created_meshes.append(obj)
                        self.report({'INFO'}, f"✓ Created {len(created_meshes)} separate objects")
            
            # Armature'ı organizasyon için mesh altında topla ve aynı konuma getir
            try:
                if created_meshes:
                    host_mesh = created_meshes[0]
                    targets = []
                    if created_armature:
                        targets.append(created_armature)
                    if created_armatures:
                        targets.extend(created_armatures)
                    for arm in targets:
                        if arm is None:
                            continue
                        # Dünya konumunu KORUYARAK mesh altına al
                        try:
                            world_mx = arm.matrix_world.copy()
                            arm.parent = host_mesh
                            arm.matrix_parent_inverse = host_mesh.matrix_world.inverted()
                            arm.matrix_world = world_mx
                        except Exception:
                            pass
            except Exception:
                pass

            # STEP 3: Import Materials (BMT)
            if settings.apply_materials and settings.bmt_file and os.path.exists(settings.bmt_file):
                self.report({'INFO'}, "Importing materials...")
                # Collect DDJ files from list
                ddj_files = [item.path for item in settings.ddj_files if item.path and os.path.exists(item.path)]
                materials = self.import_bmt(settings.bmt_file, ddj_files, settings.auto_convert_ddj)
                if materials and created_meshes:
                    for obj in created_meshes:
                        self.apply_materials_to_obj(obj, materials)
                    self.report({'INFO'}, f"✓ {len(materials)} materials applied")
            
            # STEP 4: Bind Mesh to Skeleton (BEFORE scaling!)
            if settings.bind_mesh and created_meshes and all_mesh_data:
                self.report({'INFO'}, "Binding meshes to skeleton...")
                if created_armature and not created_armatures:
                    # Single armature
                    if settings.combine_meshes:
                        self.bind_to_skeleton(created_meshes[0], created_armature, all_mesh_data)
                    else:
                        for obj, data in zip(created_meshes, all_mesh_data):
                            self.bind_to_skeleton(obj, created_armature, [data])
                elif created_armatures:
                    # Multiple armatures - bind to all of them for complete bone coverage
                    self.report({'INFO'}, f"Binding meshes to {len(created_armatures)} armatures...")
                    for i, arm in enumerate(created_armatures):
                        print(f"  Binding to armature {i+1}: {arm.name}")
                        if settings.combine_meshes:
                            self.bind_to_skeleton(created_meshes[0], arm, all_mesh_data)
                        else:
                            for obj, data in zip(created_meshes, all_mesh_data):
                                self.bind_to_skeleton(obj, arm, [data])
                self.report({'INFO'}, "✓ Meshes bound to skeleton")
            
            # STEP 5: Fit Armature to Mesh (AFTER binding!)
            # Only fit if we have meshes, otherwise skeleton stays at original scale
            if created_armature and created_meshes and not created_armatures:
                self.report({'INFO'}, "Fitting armature to mesh...")
                self.fit_armature_to_mesh(created_armature, created_meshes)
                self.report({'INFO'}, "✓ Armature fitted to mesh")
            elif created_armatures and created_meshes:
                # Multiple armatures - fit all of them to mesh with consistent scaling
                self.report({'INFO'}, "Fitting all armatures to mesh...")
                self.fit_all_armatures_to_mesh(created_armatures, created_meshes)
                self.report({'INFO'}, f"✓ {len(created_armatures)} armatures fitted to mesh")
            elif (created_armature or created_armatures) and not created_meshes:
                # Standalone skeleton - no mesh to fit to
                self.report({'INFO'}, "✓ Standalone skeleton imported (no mesh fitting)")
            
            # Mesh ve armature zaten fit_armature_to_mesh tarafından doğru konumlandırıldı
            # Koordinat dönüşümü matrix'i OBJ uyumlu (Y→-Z, Z→Y) olduğu için ek rotation gerekmez

            self.report({'INFO'}, "✅ Import completed!")
            return {'FINISHED'}
            
        except Exception as e:
            self.report({'ERROR'}, f"Import failed: {str(e)}")
            traceback.print_exc()
            return {'CANCELLED'}

    def import_bsk(self, context, filepath, split_armatures=False):
        """Import BSK skeleton - FIXED VERSION"""
        print(f"\n🦴 Importing BSK: {filepath}")
        
        try:
            with open(filepath, 'rb') as file:
                # Read signature
                signature = file.read(12).decode('utf-8', errors='replace')
                if not signature.startswith("JMXVBSK"):
                    self.report({'ERROR'}, f"Invalid BSK signature: {signature}")
                    return None
                
                print(f"  Signature: {signature}")
                
                # Read bone count
                bone_count = struct.unpack('I', file.read(4))[0]
                print(f"  Bone Count: {bone_count}")
                
                bones_data = []
                for i in range(bone_count):
                    # Bone type
                    bone_type = struct.unpack('B', file.read(1))[0]
                    
                    # Bone name
                    name_len = struct.unpack('I', file.read(4))[0]
                    name = file.read(name_len).decode('utf-8', errors='replace')
                    
                    # Parent name
                    parent_len = struct.unpack('I', file.read(4))[0]
                    parent = file.read(parent_len).decode('utf-8', errors='replace') if parent_len > 0 else ""
                    
                    # Skip rot_parent and trans_parent (not used in simple import)
                    file.read(16)  # rot_parent (quaternion)
                    file.read(12)  # trans_parent (vector3)
                    
                    # Read rot_origin and trans_origin (this is what we need!)
                    rot = struct.unpack('4f', file.read(16))
                    trans = struct.unpack('3f', file.read(12))
                    
                    # Skip rot_local and trans_local
                    file.read(16)  # rot_local
                    file.read(12)  # trans_local
                    
                    # Skip children list
                    child_count = struct.unpack('I', file.read(4))[0]
                    for j in range(child_count):
                        child_len = struct.unpack('I', file.read(4))[0]
                        file.read(child_len)
                    
                    bones_data.append({
                        'name': name,
                        'parent': parent,
                        'rotation': rot,
                        'translation': trans
                    })
                    
                    print(f"  Bone {i}: {name} → parent: {parent}")
            
            # UI'dan gelen split_armatures tercihini kullan
            return self.create_armature(context, bones_data, split_armatures)
            
        except Exception as e:
            self.report({'ERROR'}, f"BSK import failed: {e}")
            traceback.print_exc()
            return None

    def create_armature(self, context, bones_data, split_armatures=False):
        """Create armature - FIXED with proper Blender API usage"""
        print(f"  Creating armature with {len(bones_data)} bones...")
        # Index bilgisini baştan sabitle (split ve single yollarında tutarlılık için)
        for idx, b in enumerate(bones_data):
            b['index'] = idx

        # İsteğe bağlı: skeleton'ı zincirlerine göre parçalara ayır
        if split_armatures:
            # Build quick lookup
            name_to_bone = {b['name']: b for b in bones_data}
            
            # Build children map once
            children_map_tmp = {}
            for bone in bones_data:
                p = bone.get('parent') or ""
                if p not in children_map_tmp:
                    children_map_tmp[p] = []
                children_map_tmp[p].append(bone)

            # Identify top-level roots (no parent in list)
            toplvl_roots = []
            for b in bones_data:
                parent_name = b.get('parent') or ""
                if not parent_name or parent_name not in name_to_bone:
                    toplvl_roots.append(b['name'])

            # Helper: walk down while there is exactly one child to find first branching node
            def descend_to_first_branch(name:str)->str:
                current = name
                while True:
                    children = children_map_tmp.get(current, [])
                    if len(children) != 1:
                        return current
                    current = children[0]['name']

            split_roots = []
            # If multiple top-level roots, split by them
            if len(toplvl_roots) > 1:
                split_roots = toplvl_roots
            else:
                # Single root: find first branching node then split by its children
                single_root = toplvl_roots[0] if toplvl_roots else None
                target_branch = single_root
                if single_root is not None:
                    target_branch = descend_to_first_branch(single_root)

                # If UI allows forcing child split, or we found a branching node
                try:
                    split_children_pref = bpy.context.scene.game_importer_settings.split_root_children
                except Exception:
                    split_children_pref = True

                if target_branch is not None and split_children_pref:
                    branch_children = children_map_tmp.get(target_branch, [])
                    if branch_children:
                        split_roots = [c['name'] for c in branch_children]

            # Fallback: if still empty, keep single armature behavior
            if not split_roots:
                split_roots = toplvl_roots

            print(f"  Splitting skeleton into {len(split_roots)} armature objects (by dynamic branches)...")

            created_armatures = []

            def collect_subtree(root_name):
                selected = []
                children_map = {}
                for bone in bones_data:
                    p = bone.get('parent') or ""
                    if p not in children_map:
                        children_map[p] = []
                    children_map[p].append(bone)

                stack = [root_name]
                seen = set()
                while stack:
                    current = stack.pop()
                    if current in seen:
                        continue
                    seen.add(current)
                    bone = name_to_bone.get(current)
                    if bone:
                        selected.append(bone)
                        for child in children_map.get(current, []):
                            stack.append(child['name'])
                return selected

            for root in split_roots:
                subtree = collect_subtree(root)
                if not subtree:
                    continue
                arm_obj = self._create_single_armature(context, subtree)
                if arm_obj:
                    created_armatures.append(arm_obj)

            # Ek olarak: Tam iskeleti de tek bir armature olarak oluştur (bind için referans)
            # Böylece mesh tek Armature modifier ile doğru indeksleme ile deforme olur
            combined_armature = self._create_single_armature(context, bones_data)

            # İlk eleman tam armature olacak şekilde döndür (bind buna yapılacak)
            return [combined_armature] + created_armatures if combined_armature else created_armatures
        
        # TEK ARMATURE OLUŞTUR - Tüm kemikleri tek armature'da tut
        print(f"  Creating single armature with all {len(bones_data)} bones...")
        print(f"  ✓ Skeleton bölünmesi devre dışı - tüm kemikler tek armature'da")
        
        # Deselect all
        # Deselect all objects safely
        for obj in bpy.context.scene.objects:
            obj.select_set(False)
        
        # Create armature
        bpy.ops.object.armature_add()
        armature_obj = bpy.context.object
        armature_obj.name = "ImportedSkeleton"
        armature_data = armature_obj.data
        
        # Enter EDIT mode
        bpy.ops.object.mode_set(mode='EDIT')
        
        # Remove default bone
        armature_data.edit_bones.remove(armature_data.edit_bones[0])
        
        # Build parent-child map and bone index map
        children_map = {}
        bone_by_name = {}
        
        for idx, bone in enumerate(bones_data):
            bone['index'] = idx  # Store original index
            bone_by_name[bone['name']] = bone
            
            if bone['parent']:
                if bone['parent'] not in children_map:
                    children_map[bone['parent']] = []
                children_map[bone['parent']].append(bone)
        
        # Create all bones with proper transforms
        bone_map = {}
        for bone_data in bones_data:
            edit_bone = armature_data.edit_bones.new(bone_data['name'])
            
            # Get bone transform data
            tx, ty, tz = bone_data['translation']
            qx, qy, qz, qw = bone_data['rotation']
            
            # SRO→Blender dönüşümü uygula
            head_bl = convert_vec_sro_to_blender(Vector((tx, ty, tz)))
            quat_sro = Quaternion((qw, qx, qy, qz))
            quat_bl = convert_quat_sro_to_blender(quat_sro)
            
            # Set head position (dönüştürülmüş)
            edit_bone.head = head_bl
            
            # Calculate tail position intelligently
            if bone_data['name'] in children_map:
                # If has children, point to average of children positions
                children = children_map[bone_data['name']]
                if len(children) == 1:
                    # Point to single child (dönüştürülmüş)
                    cx, cy, cz = children[0]['translation']
                    child_pos_bl = convert_vec_sro_to_blender(Vector((cx, cy, cz)))
                    edit_bone.tail = child_pos_bl
                else:
                    # Point to average of children (dönüştürülmüş)
                    avg_pos = Vector()
                    for child in children:
                        cx, cy, cz = child['translation']
                        child_pos_bl = convert_vec_sro_to_blender(Vector((cx, cy, cz)))
                        avg_pos += child_pos_bl
                    avg_pos /= len(children)
                    edit_bone.tail = avg_pos
            else:
                # Leaf bone - use rotation and sensible length
                bone_length = 5.0  # Default base length
                
                # If has parent, use parent's length as reference
                if bone_data['parent'] and bone_data['parent'] in bone_map:
                    parent_bone = bone_map[bone_data['parent']]
                    parent_length = (parent_bone.tail - parent_bone.head).length
                    bone_length = parent_length * 0.5
                
                # Apply rotation to determine tail direction (dönüştürülmüş quaternion kullan)
                direction = quat_bl.to_matrix() @ Vector((0, bone_length, 0))
                edit_bone.tail = edit_bone.head + direction
            
            # Ensure minimum bone length (Blender requirement)
            if (edit_bone.tail - edit_bone.head).length < 0.001:
                edit_bone.tail = edit_bone.head + Vector((0, 0.1, 0))
            
            bone_map[bone_data['name']] = edit_bone
        
        # Set parent relationships
        for bone_data in bones_data:
            if bone_data['parent'] and bone_data['parent'] in bone_map:
                bone = bone_map[bone_data['name']]
                parent = bone_map[bone_data['parent']]
                bone.parent = parent
                
                # Auto-connect if head matches parent tail
                if (bone.head - parent.tail).length < 0.01:
                    bone.use_connect = True
        
        # Back to OBJECT mode
        # Set mode safely
        if bpy.context.active_object and bpy.context.active_object.type == 'ARMATURE':
            bpy.ops.object.mode_set(mode='OBJECT')
        
        # Verify bone count
        actual_bone_count = len(armature_data.bones)
        print(f"  ✓ Armature created: {actual_bone_count} bones (expected {len(bones_data)})")
        
        if actual_bone_count != len(bones_data):
            print(f"  ⚠️  WARNING: Bone count mismatch!")
        
        # Armature global dönüşümlerini sabitle (mesh ile hizalı başlasın)
        armature_obj.location = (0.0, 0.0, 0.0)
        armature_obj.rotation_euler = (0.0, 0.0, 0.0)
        armature_obj.scale = (1.0, 1.0, 1.0)

        # Display settings - optimized for standalone skeleton viewing
        armature_obj.show_in_front = True
        armature_data.display_type = 'OCTAHEDRAL'  # Better visibility
        
        # Show bone names and axes for debugging
        armature_data.show_names = True
        armature_data.show_axes = True
        
        # Armature already linked by bpy.ops.object.armature_add()
        
        return armature_obj

    def _create_single_armature(self, context, bones_subset):
        """Create a single armature object from the provided bones subset."""
        # Ensure stable order: preserve original index order if available
        ordered = list(bones_subset)
        ordered.sort(key=lambda b: b.get('index', 0))

        # Deselect all
        # Deselect all objects safely
        for obj in bpy.context.scene.objects:
            obj.select_set(False)

        # Create armature
        bpy.ops.object.armature_add()
        armature_obj = bpy.context.object
        armature_obj.name = ordered[0]['name'] if ordered else "ImportedSkeletonPart"
        armature_data = armature_obj.data

        # Enter EDIT mode
        bpy.ops.object.mode_set(mode='EDIT')

        # Remove default bone
        if armature_data.edit_bones:
            armature_data.edit_bones.remove(armature_data.edit_bones[0])

        # Build parent-child map
        children_map = {}
        for bone in ordered:
            p = bone.get('parent') or ""
            if p not in children_map:
                children_map[p] = []
            children_map[p].append(bone)

        # Create all bones
        bone_map = {}
        for bone_data in ordered:
            edit_bone = armature_data.edit_bones.new(bone_data['name'])

            tx, ty, tz = bone_data['translation']
            qx, qy, qz, qw = bone_data['rotation']

            # SRO→Blender dönüşümü uygula
            head_bl = convert_vec_sro_to_blender(Vector((tx, ty, tz)))
            quat_sro = Quaternion((qw, qx, qy, qz))
            quat_bl = convert_quat_sro_to_blender(quat_sro)

            edit_bone.head = head_bl

            if bone_data['name'] in children_map and children_map[bone_data['name']]:
                children = children_map[bone_data['name']]
                if len(children) == 1:
                    cx, cy, cz = children[0]['translation']
                    child_pos_bl = convert_vec_sro_to_blender(Vector((cx, cy, cz)))
                    edit_bone.tail = child_pos_bl
                else:
                    avg_pos = Vector()
                    for child in children:
                        cx, cy, cz = child['translation']
                        child_pos_bl = convert_vec_sro_to_blender(Vector((cx, cy, cz)))
                        avg_pos += child_pos_bl
                    avg_pos /= len(children)
                    edit_bone.tail = avg_pos
            else:
                bone_length = 5.0
                direction = quat_bl.to_matrix() @ Vector((0, bone_length, 0))
                edit_bone.tail = edit_bone.head + direction

            if (edit_bone.tail - edit_bone.head).length < 0.001:
                edit_bone.tail = edit_bone.head + Vector((0, 0.1, 0))

            bone_map[bone_data['name']] = edit_bone

        # Parent relationships
        for bone_data in ordered:
            parent_name = bone_data.get('parent') or ""
            if parent_name and parent_name in bone_map and bone_data['name'] in bone_map:
                bone = bone_map[bone_data['name']]
                parent = bone_map[parent_name]
                bone.parent = parent
                if (bone.head - parent.tail).length < 0.01:
                    bone.use_connect = True

        # Back to OBJECT mode
        # Set mode safely
        if bpy.context.active_object and bpy.context.active_object.type == 'ARMATURE':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Armature global dönüşümlerini sabitle (mesh ile hizalı başlasın)
        armature_obj.location = (0.0, 0.0, 0.0)
        armature_obj.rotation_euler = (0.0, 0.0, 0.0)
        armature_obj.scale = (1.0, 1.0, 1.0)

        # Display settings
        armature_obj.show_in_front = True
        armature_data.display_type = 'OCTAHEDRAL'
        armature_data.show_names = True
        armature_data.show_axes = True

        # Armature already linked by bpy.ops.object.armature_add()

        return armature_obj

    def import_bms(self, filepath):
        """Import BMS mesh - FIXED"""
        print(f"\n📦 Importing BMS: {filepath}")
        
        with open(filepath, 'rb') as file:
            # Signature check
            sig = file.read(4).decode('utf-8')
            if not sig.startswith("JMXV"):
                raise ValueError("Invalid BMS signature")
            
            # Version
            file.read(8)
            
            # Header
            header = struct.unpack('12I', file.read(48))
            vertex_offset = header[0]
            face_offset = header[2]
            
            # Flags and names
            file.read(4)  # sub_prim_count
            vertex_flag = struct.unpack('I', file.read(4))[0]
            file.read(4)  # unk
            
            # Mesh name
            name_len = struct.unpack('I', file.read(4))[0]
            name = file.read(name_len).decode('utf-8', errors='replace') if name_len > 0 else "Mesh"
            
            # Material name
            mat_len = struct.unpack('I', file.read(4))[0]
            mat_name = file.read(mat_len).decode('utf-8', errors='replace') if mat_len > 0 else ""
            
            # Read vertices
            file.seek(vertex_offset)
            vert_count = struct.unpack('I', file.read(4))[0]
            
            verts = []
            uvs = []
            skins = []
            
            for i in range(vert_count):
                # Position (SRO -> Blender dönüşümü uygula)
                pos = struct.unpack('3f', file.read(12))
                p_bl = convert_vec_sro_to_blender(Vector(pos))
                verts.append((p_bl.x, p_bl.y, p_bl.z))
                
                # Normal (skip)
                file.read(12)
                
                # UV
                uv = struct.unpack('2f', file.read(8))
                uvs.append((uv[0], 1.0 - uv[1]))  # Flip V
                
                # Skip float
                file.read(4)
                
                # Skin data
                indices = struct.unpack('4B', file.read(4))
                weights_raw = struct.unpack('4B', file.read(4))
                total = sum(weights_raw)
                weights = [w / total for w in weights_raw] if total > 0 else [0.0] * 4
                skins.append({'indices': indices, 'weights': weights})
                
                # Additional data based on flags
                if vertex_flag & 0x400:
                    file.read(8)  # uv1
                if vertex_flag & 0x800:
                    file.read(32)  # morph
            
            # Read faces
            file.seek(face_offset)
            face_count = struct.unpack('I', file.read(4))[0]
            
            faces = []
            for i in range(face_count):
                face = struct.unpack('3H', file.read(6))
                faces.append(face)
            
            print(f"  ✓ {name}: {vert_count} verts, {face_count} faces")
            
            # Material assignment info
            if mat_name:
                print(f"    → Material: '{mat_name}'")
            
            return {
                'name': name,
                'vertices': verts,
                'faces': faces,
                'uvs': uvs,
                'skin_data': skins,
                'material_name': mat_name
            }

    def create_mesh_object(self, context, mesh_data):
        """Create mesh object - FIXED"""
        mesh = bpy.data.meshes.new(mesh_data['name'])
        mesh.from_pydata(mesh_data['vertices'], [], mesh_data['faces'])
        
        # Add UVs
        if mesh_data.get('uvs'):
            uv_layer = mesh.uv_layers.new(name="UVMap")
            for i, loop in enumerate(mesh.loops):
                uv_layer.data[i].uv = mesh_data['uvs'][loop.vertex_index]
        
        mesh.update()
        
        obj = bpy.data.objects.new(mesh_data['name'], mesh)
        context.collection.objects.link(obj)
        # Mesh ve skeleton aynı konumda başlasın - rotasyon yok
        obj.location = (0.0, 0.0, 0.0)
        obj.rotation_euler = (0.0, 0.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)
        
        # Store material name as custom property for later matching
        if mesh_data.get('material_name'):
            obj['material_name'] = mesh_data['material_name']
        
        return obj

    def create_combined_mesh(self, context, mesh_data_list, name):
        """Create combined mesh - FIXED"""
        all_verts = []
        all_faces = []
        all_uvs = []
        all_skins = []
        v_offset = 0
        
        for data in mesh_data_list:
            all_verts.extend(data['vertices'])
            if data.get('uvs'):
                all_uvs.extend(data['uvs'])
            if data.get('skin_data'):
                all_skins.extend(data['skin_data'])
            
            for face in data['faces']:
                all_faces.append(tuple(i + v_offset for i in face))
            
            v_offset += len(data['vertices'])
        
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(all_verts, [], all_faces)
        
        # Add UVs
        if all_uvs:
            uv_layer = mesh.uv_layers.new(name="UVMap")
            for i, loop in enumerate(mesh.loops):
                uv_layer.data[i].uv = all_uvs[loop.vertex_index]
        
        mesh.update()
        
        obj = bpy.data.objects.new(name, mesh)
        context.collection.objects.link(obj)
        # Mesh ve skeleton aynı konumda başlasın - rotasyon yok
        obj.location = (0.0, 0.0, 0.0)
        obj.rotation_euler = (0.0, 0.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)
        
        # Store skin data for later binding
        obj['combined_skin_data'] = all_skins
        obj['mesh_data_list'] = mesh_data_list
        
        # Combined mesh için vertex group'ları oluştur (Auto Weights başarısız olursa)
        if all_skins:
            # Tüm kemik isimlerini topla
            bone_names = set()
            for skin in all_skins:
                for bone_idx in skin['indices']:
                    if bone_idx != 255 and bone_idx != 0xFF:
                        bone_names.add(f"Bone_{bone_idx}")  # Geçici isim
            
            # Vertex group'ları oluştur
            for bone_name in bone_names:
                if bone_name not in obj.vertex_groups:
                    obj.vertex_groups.new(name=bone_name)
            
            print(f"  DEBUG: Combined mesh için {len(bone_names)} vertex group oluşturuldu")
        
        return obj

    def fit_armature_to_mesh(self, armature_obj, mesh_objects):
        """Fit armature to mesh - Scale and position armature to match mesh"""
        print(f"\n🔧 Fitting armature to mesh...")
        
        # Calculate combined mesh bounding box (in local space)
        min_bound = Vector((float('inf'), float('inf'), float('inf')))
        max_bound = Vector((float('-inf'), float('-inf'), float('-inf')))
        
        for mesh_obj in mesh_objects:
            for vert in mesh_obj.data.vertices:
                # Use local coordinates since both mesh and armature are at 0,0,0
                min_bound.x = min(min_bound.x, vert.co.x)
                min_bound.y = min(min_bound.y, vert.co.y)
                min_bound.z = min(min_bound.z, vert.co.z)
                max_bound.x = max(max_bound.x, vert.co.x)
                max_bound.y = max(max_bound.y, vert.co.y)
                max_bound.z = max(max_bound.z, vert.co.z)
        
        mesh_size = max_bound - min_bound
        mesh_center = (min_bound + max_bound) / 2
        
        print(f"  Mesh bounds: {min_bound} to {max_bound}")
        print(f"  Mesh size: {mesh_size}")
        print(f"  Mesh center: {mesh_center}")
        
        # Calculate armature bounding box
        # Deselect all objects safely
        for obj in bpy.context.scene.objects:
            obj.select_set(False)
        
        armature_obj.select_set(True)
        bpy.context.view_layer.objects.active = armature_obj
        # Set mode safely
        if bpy.context.active_object and bpy.context.active_object.type == 'ARMATURE':
            bpy.ops.object.mode_set(mode='EDIT')
        
        arm_min = Vector((float('inf'), float('inf'), float('inf')))
        arm_max = Vector((float('-inf'), float('-inf'), float('-inf')))
        
        for bone in armature_obj.data.edit_bones:
            for point in [bone.head, bone.tail]:
                # Use local coordinates (armature at 1/1/1 scale)
                arm_min.x = min(arm_min.x, point.x)
                arm_min.y = min(arm_min.y, point.y)
                arm_min.z = min(arm_min.z, point.z)
                arm_max.x = max(arm_max.x, point.x)
                arm_max.y = max(arm_max.y, point.y)
                arm_max.z = max(arm_max.z, point.z)
        
        # Set mode safely
        if bpy.context.active_object and bpy.context.active_object.type == 'ARMATURE':
            bpy.ops.object.mode_set(mode='OBJECT')
        
        arm_size = arm_max - arm_min
        arm_center = (arm_min + arm_max) / 2
        
        print(f"  Armature bounds: {arm_min} to {arm_max}")
        print(f"  Armature size: {arm_size}")
        print(f"  Armature center: {arm_center}")
        
        # Calculate scale factor (make armature match mesh size)
        # Use largest dimension to maintain proportions
        mesh_max_dim = max(mesh_size.x, mesh_size.y, mesh_size.z)
        arm_max_dim = max(arm_size.x, arm_size.y, arm_size.z)
        
        if arm_max_dim > 0.001 and mesh_max_dim > 0.001:
            scale_factor = mesh_max_dim / arm_max_dim
            
            # Apply scale to armature
            armature_obj.scale = (scale_factor, scale_factor, scale_factor)
            # Apply scale (prevent animation-time double transform)
            # Deselect all, select armature and apply scale only
            for obj in bpy.context.scene.objects:
                obj.select_set(False)
            armature_obj.select_set(True)
            bpy.context.view_layer.objects.active = armature_obj
            try:
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            except Exception:
                pass
            
            # Merkez hizalama - her iki obje de 0,0,0'da olduğu için offset hesapla
            offset = mesh_center - (arm_center * scale_factor)
            armature_obj.location = offset
            
            print(f"  ✓ Scale factor: {scale_factor:.4f}")
            print(f"  ✓ Location offset: {offset}")
        else:
            print(f"  ⚠️ Cannot calculate scale factor, keeping original sizes")
    
    def fit_all_armatures_to_mesh(self, armature_objects, mesh_objects):
        """Fit all armatures to mesh - Scale and position armatures to match mesh"""
        if not armature_objects or not mesh_objects:
            return
            
        print(f"\n🔧 Fitting {len(armature_objects)} armatures to mesh...")
        
        # Calculate mesh bounding box once (in local space)
        min_bound = Vector((float('inf'), float('inf'), float('inf')))
        max_bound = Vector((float('-inf'), float('-inf'), float('-inf')))
        
        for mesh_obj in mesh_objects:
            for vert in mesh_obj.data.vertices:
                # Use local coordinates since both mesh and armature are at 0,0,0
                min_bound.x = min(min_bound.x, vert.co.x)
                min_bound.y = min(min_bound.y, vert.co.y)
                min_bound.z = min(min_bound.z, vert.co.z)
                max_bound.x = max(max_bound.x, vert.co.x)
                max_bound.y = max(max_bound.y, vert.co.y)
                max_bound.z = max(max_bound.z, vert.co.z)
        
        mesh_size = max_bound - min_bound
        mesh_center = (min_bound + max_bound) / 2
        mesh_max_dim = max(mesh_size.x, mesh_size.y, mesh_size.z)
        
        print(f"  Mesh bounds: {min_bound} to {max_bound}")
        print(f"  Mesh size: {mesh_size}")
        print(f"  Mesh center: {mesh_center}")
        
        # Calculate scale factor from first armature, then apply to all
        first_arm = armature_objects[0]
        # Deselect all objects safely
        for obj in bpy.context.scene.objects:
            obj.select_set(False)
        first_arm.select_set(True)
        bpy.context.view_layer.objects.active = first_arm
        # Set mode safely
        if bpy.context.active_object and bpy.context.active_object.type == 'ARMATURE':
            bpy.ops.object.mode_set(mode='EDIT')
        
        arm_min = Vector((float('inf'), float('inf'), float('inf')))
        arm_max = Vector((float('-inf'), float('-inf'), float('-inf')))
        
        for bone in first_arm.data.edit_bones:
            for point in [bone.head, bone.tail]:
                # Use local coordinates (armature at 1/1/1 scale)
                arm_min.x = min(arm_min.x, point.x)
                arm_min.y = min(arm_min.y, point.y)
                arm_min.z = min(arm_min.z, point.z)
                arm_max.x = max(arm_max.x, point.x)
                arm_max.y = max(arm_max.y, point.y)
                arm_max.z = max(arm_max.z, point.z)
        
        # Set mode safely
        if bpy.context.active_object and bpy.context.active_object.type == 'ARMATURE':
            bpy.ops.object.mode_set(mode='OBJECT')
        
        arm_size = arm_max - arm_min
        arm_center = (arm_min + arm_max) / 2
        arm_max_dim = max(arm_size.x, arm_size.y, arm_size.z)
        
        # Calculate scale factor (make armature match mesh size)
        if arm_max_dim > 0.001 and mesh_max_dim > 0.001:
            scale_factor = mesh_max_dim / arm_max_dim
            
            # Apply same scale and offset to all armatures
            for i, arm in enumerate(armature_objects):
                arm.scale = (scale_factor, scale_factor, scale_factor)
                # Apply scale to each armature to avoid double transform at animation time
                for obj in bpy.context.scene.objects:
                    obj.select_set(False)
                arm.select_set(True)
                bpy.context.view_layer.objects.active = arm
                try:
                    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
                except Exception:
                    pass
                offset = mesh_center - (arm_center * scale_factor)
                arm.location = offset
                print(f"  ✓ Armature {i+1} ({arm.name}): scale={scale_factor:.4f}, offset={offset}")
            
            print(f"  ✓ Scale factor: {scale_factor:.4f} (applied to all armatures)")
            print(f"  ✓ All armatures positioned at mesh center")
        else:
            print(f"  ⚠️ Cannot calculate scale factor, keeping original sizes")
    
    def bind_to_skeleton(self, mesh_obj, armature_obj, mesh_data_list):
        """Bind mesh to skeleton - FIXED with proper bone indexing"""
        print(f"\n🔗 Binding {mesh_obj.name} to {armature_obj.name}")
        
        # Helper: run an operator with a safe VIEW_3D override
        def run_with_view3d_override(op_callable, **kwargs):
            try:
                win = bpy.context.window
                scr = win.screen if win else None
                area = None
                region = None
                if scr:
                    for a in scr.areas:
                        if a.type == 'VIEW_3D':
                            area = a
                            break
                    if area:
                        for r in area.regions:
                            if r.type == 'WINDOW':
                                region = r
                                break
                if area and region and win:
                    with bpy.context.temp_override(window=win, screen=scr, area=area, region=region, view_layer=bpy.context.view_layer, scene=bpy.context.scene, object=mesh_obj):
                        return op_callable(**kwargs)
                else:
                    return op_callable(**kwargs)
            except Exception as e:
                print(f"  DEBUG: run_with_view3d_override failed: {e}")
                try:
                    return op_callable(**kwargs)
                except Exception as e2:
                    print(f"  DEBUG: operator call failed without override: {e2}")
            return None
        
        # Eğer mesh'te zaten aktif bir Armature modifier varsa, ek modifier eklemeyelim.
        # Split senaryosunda birden fazla armature aynı mesh'i aynı anda deforme ederse
        # animasyon sırasında "patlama" yaşanır.
        for m in mesh_obj.modifiers:
            if m.type == 'ARMATURE' and m.object is not None:
                print(f"  DEBUG: {mesh_obj.name} already bound to {m.object.name}, skipping additional armature '{armature_obj.name}'")
                return
        
        # DEBUG: Check bone count and names
        bone_count = len(armature_obj.data.bones)
        bone_names = [bone.name for bone in armature_obj.data.bones]
        print(f"  DEBUG: Armature has {bone_count} bones")
        print(f"  DEBUG: Bone index range: 0-{bone_count-1}")
        print(f"  DEBUG: Bone names: {bone_names[:10]}...")  # Show first 10
        print(f"  DEBUG: All bone names: {bone_names}")  # Show ALL bone names
        
        # Create bone name to index mapping for fallback
        bone_name_to_index = {name: i for i, name in enumerate(bone_names)}
        
        # Create smart bone mapping for high indices
        # Group bones by type for better mapping
        spine_bones = [i for i, name in enumerate(bone_names) if 'Spine' in name or 'Neck' in name or 'Head' in name]
        arm_bones = [i for i, name in enumerate(bone_names) if 'Arm' in name or 'Hand' in name or 'Finger' in name or 'Clavicle' in name]
        leg_bones = [i for i, name in enumerate(bone_names) if 'Thigh' in name or 'Calf' in name or 'Foot' in name or 'Toe' in name or 'HorseLink' in name]
        tail_bones = [i for i, name in enumerate(bone_names) if 'Tail' in name or 'Ponytail' in name]
        other_bones = [i for i, name in enumerate(bone_names) if name.startswith('Bone')]
        
        print(f"  DEBUG: Bone groups - Spine: {len(spine_bones)}, Arm: {len(arm_bones)}, Leg: {len(leg_bones)}, Tail: {len(tail_bones)}, Other: {len(other_bones)}")
        
        # Parent KALDIRILDI: Split çoklu armature senaryosunda tek bir armature'a
        # parent etmek global dönüşlerde çakışma yaratabiliyor. Sadece modifier kullan.
        
        # Add armature modifier
        mod = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
        mod.object = armature_obj
        mod.use_deform_preserve_volume = True  # Better deformation
        print(f"  DEBUG: Armature modifier added: {mod.name}")

        # Mesh zaten 0,0,0'da ve rotasyon yok, transform uygulamaya gerek yok
        
        # Create vertex groups for all bones
        for bone in armature_obj.data.bones:
            if bone.name not in mesh_obj.vertex_groups:
                mesh_obj.vertex_groups.new(name=bone.name)
        
        # Create bone name to local index mapping
        bone_name_to_local_index = {}
        for i, bone in enumerate(armature_obj.data.bones):
            bone_name_to_local_index[bone.name] = i
        
        # Apply skin weights - with proper bone index handling
        v_offset = 0
        total_weights = 0
        successful_weights = 0
        failed_bones = set()
        out_of_range_count = 0
        skipped_empty = 0
        
        for data in mesh_data_list:
            skin_data = data.get('skin_data')
            if not skin_data:
                v_offset += len(data['vertices'])
                continue
            
            # DEBUG: Analyze skin data to understand bone index range
            all_bone_indices = set()
            for skin in skin_data:
                for bone_idx in skin['indices']:
                    if bone_idx != 255 and bone_idx != 0xFF:
                        all_bone_indices.add(bone_idx)
            
            if all_bone_indices:
                print(f"  DEBUG: {data.get('name', 'Unknown')} skin data contains bone indices: {sorted(all_bone_indices)}")
                print(f"  DEBUG: Min bone index: {min(all_bone_indices)}")
                print(f"  DEBUG: Max bone index: {max(all_bone_indices)}")
            
            for v_idx, skin in enumerate(skin_data):
                for i in range(4):
                    bone_idx = skin['indices'][i]
                    weight = skin['weights'][i]
                    
                    # Skip empty slots (0xFF = 255)
                    if bone_idx == 255 or bone_idx == 0xFF:
                        if weight > 0:
                            skipped_empty += 1
                        continue
                    
                    # Skip zero weights
                    if weight <= 0.001:
                        continue
                    
                    total_weights += 1
                    
                    # Bone index mapping with fallback
                    try:
                        if bone_idx < len(armature_obj.data.bones):
                            # Direct mapping
                            bone_name = armature_obj.data.bones[bone_idx].name
                            vg = mesh_obj.vertex_groups.get(bone_name)
                            if vg:
                                vg.add([v_idx + v_offset], weight, 'ADD')
                                successful_weights += 1
                            else:
                                failed_bones.add(bone_idx)
                        else:
                            out_of_range_count += 1
                            # Index out of range - try smart mapping based on bone type
                            # Map high indices to similar bone types
                            if bone_idx >= 77:
                                # High indices: map to appropriate bone group
                                offset = bone_idx - 77
                                
                                # Try to map to spine bones first (most common)
                                if spine_bones and offset < len(spine_bones):
                                    mapped_idx = spine_bones[offset % len(spine_bones)]
                                elif arm_bones and offset < len(arm_bones) * 2:
                                    mapped_idx = arm_bones[(offset - len(spine_bones)) % len(arm_bones)]
                                elif leg_bones and offset < len(leg_bones) * 2:
                                    mapped_idx = leg_bones[(offset - len(spine_bones) - len(arm_bones)) % len(leg_bones)]
                                elif other_bones:
                                    mapped_idx = other_bones[offset % len(other_bones)]
                                else:
                                    # Fallback: use modulo mapping
                                    mapped_idx = (bone_idx - 77) % (len(armature_obj.data.bones) - 1) + 1
                            else:
                                # Very high indices: use modulo mapping
                                mapped_idx = bone_idx % len(armature_obj.data.bones)
                            
                            bone_name = armature_obj.data.bones[mapped_idx].name
                            
                            vg = mesh_obj.vertex_groups.get(bone_name)
                            if vg:
                                vg.add([v_idx + v_offset], weight, 'ADD')
                                successful_weights += 1
                                if bone_idx not in failed_bones:  # Only print once per unique index
                                    print(f"  DEBUG: Bone index {bone_idx} mapped to {mapped_idx} ({bone_name})")
                            else:
                                failed_bones.add(bone_idx)
                    except (IndexError, KeyError) as e:
                        print(f"  DEBUG: Exception for bone_idx {bone_idx}: {e}")
                        failed_bones.add(bone_idx)
            
            v_offset += len(data['vertices'])
        
        # Sonuç raporu
        print(f"  DEBUG: Total processed weights: {total_weights}")
        print(f"  DEBUG: Successful weights: {successful_weights}")
        print(f"  DEBUG: Failed bones: {len(failed_bones)}")
        print(f"  DEBUG: Skipped empty slots: {skipped_empty}")
        out_of_range_ratio = (out_of_range_count / max(1, total_weights)) if total_weights > 0 else 0.0
        if out_of_range_count:
            print(f"  DEBUG: Out-of-range indices: {out_of_range_count} (ratio={out_of_range_ratio:.2f})")
        
        if failed_bones:
            failed_count = len(failed_bones)
            success_rate = (successful_weights / total_weights * 100) if total_weights > 0 else 0
            failed_list = sorted(list(failed_bones))[:10]
            print(f"  ⚠️  {failed_count} geçersiz bone index: {failed_list}")
            print(f"  ✓ Binding: {successful_weights}/{total_weights} weight (%{success_rate:.1f})")
        
        if skipped_empty > 0:
            print(f"  ℹ️  {skipped_empty} boş slot atlandı (0xFF)")
        
        if not failed_bones and skipped_empty == 0:
            print(f"  ✓ Binding complete: {successful_weights} weights applied")

        # Güvenli fallback: Aşırı oranda out-of-range varsa otomatik ağırlık kullan
        try:
            if out_of_range_ratio >= 0.20:
                print("  ⚠️  High out-of-range ratio detected → using Auto Weights fallback")
                # Temiz bir başlangıç: mevcut vertex gruplarını sil
                mesh_obj.vertex_groups.clear()
                # Armature'u OBJECT moda al ve poz dönüşümlerini temizle
                bpy.context.view_layer.objects.active = armature_obj
                try:
                    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
                        bpy.ops.object.mode_set(mode='OBJECT')
                    bpy.ops.object.mode_set(mode='POSE')
                    bpy.ops.pose.select_all(action='SELECT')
                    bpy.ops.pose.rot_clear()
                    bpy.ops.pose.loc_clear()
                    bpy.ops.pose.scale_clear()
                except Exception:
                    pass
                if bpy.context.object and bpy.context.object.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                # Armature veri bloğunu Pose pozisyonuna zorla
                try:
                    armature_obj.data.pose_position = 'POSE'
                except Exception:
                    pass

                # Seçimleri ayarla
                for obj in bpy.context.scene.objects:
                    obj.select_set(False)
                armature_obj.select_set(True)
                mesh_obj.select_set(True)
                bpy.context.view_layer.objects.active = mesh_obj
                # Otomatik ağırlık ile parent-set (modifier ve parent gelir) - güvenli override ile
                run_with_view3d_override(bpy.ops.object.parent_set, type='ARMATURE_AUTO')
                # Ebeveynliği kaldır, sadece modifier kalsın
                mesh_obj.parent = None
                # Yalnızca tek Armature modifier bırak ve doğru objeye ayarla
                arm_mods = [m for m in mesh_obj.modifiers if m.type == 'ARMATURE']
                # İlkini tut, diğerlerini sil
                keep = None
                for m in arm_mods:
                    if keep is None:
                        keep = m
                    else:
                        try:
                            mesh_obj.modifiers.remove(m)
                        except Exception:
                            pass
                if keep is None:
                    keep = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
                keep.object = armature_obj
                keep.use_deform_preserve_volume = True
                keep.use_vertex_groups = True
                keep.use_bone_envelopes = False
                # Modifier'ı en üste güvenli taşı (sonsuz döngü önleme ve doğru context)
                try:
                    # OBJECT moda ve aktif obje mesh olsun
                    for obj in bpy.context.scene.objects:
                        obj.select_set(False)
                    mesh_obj.select_set(True)
                    bpy.context.view_layer.objects.active = mesh_obj
                    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
                        bpy.ops.object.mode_set(mode='OBJECT')

                    def get_index():
                        for i, m in enumerate(mesh_obj.modifiers):
                            if m is keep:
                                return i
                        return -1

                    max_attempts = len(mesh_obj.modifiers) + 2
                    attempts = 0
                    last_index = get_index()
                    while get_index() > 0 and attempts < max_attempts:
                        try:
                            bpy.ops.object.modifier_move_up(modifier=keep.name)
                        except Exception:
                            break
                        idx = get_index()
                        if idx == last_index:
                            # İlerleme yoksa kır
                            break
                        last_index = idx
                        attempts += 1
                except Exception:
                    pass
                # Vertex group kontrolü; yoksa bir kez daha Auto Weights dene
                if len(mesh_obj.vertex_groups) == 0:
                    try:
                        for obj in bpy.context.scene.objects:
                            obj.select_set(False)
                        armature_obj.select_set(True)
                        mesh_obj.select_set(True)
                        bpy.context.view_layer.objects.active = mesh_obj
                        run_with_view3d_override(bpy.ops.object.parent_set, type='ARMATURE_AUTO')
                        mesh_obj.parent = None
                    except Exception:
                        pass
                print("  ✓ Auto Weights applied; single Armature modifier configured and moved to top")
            
            # Auto Weights başarısız olduysa manuel vertex group oluştur
            if len(mesh_obj.vertex_groups) == 0:
                print("  ⚠️  Auto Weights failed → creating manual vertex groups")
                # Tüm kemikler için vertex group oluştur
                for bone in armature_obj.data.bones:
                    if bone.name not in mesh_obj.vertex_groups:
                        mesh_obj.vertex_groups.new(name=bone.name)
                
                # Skin data varsa ağırlıkları uygula
                if hasattr(mesh_obj, 'combined_skin_data') and mesh_obj['combined_skin_data']:
                    skin_data = mesh_obj['combined_skin_data']
                    for v_idx, skin in enumerate(skin_data):
                        for i in range(4):
                            bone_idx = skin['indices'][i]
                            weight = skin['weights'][i]
                            
                            if bone_idx != 255 and bone_idx != 0xFF and weight > 0.001:
                                if bone_idx < len(armature_obj.data.bones):
                                    bone_name = armature_obj.data.bones[bone_idx].name
                                    vg = mesh_obj.vertex_groups.get(bone_name)
                                    if vg:
                                        vg.add([v_idx], weight, 'ADD')
                else:
                    # Skin data yoksa en yakın kemiğe ağırlık ata
                    print("  ⚠️  No skin data → assigning weights to nearest bones")
                    # Her vertex için en yakın kemiği bul ve ağırlık ata
                    for v_idx in range(len(mesh_obj.data.vertices)):
                        vert_pos = mesh_obj.data.vertices[v_idx].co
                        min_dist = float('inf')
                        closest_bone = None
                        
                        for bone in armature_obj.data.bones:
                            bone_pos = bone.head_local
                            dist = (vert_pos - bone_pos).length
                            if dist < min_dist:
                                min_dist = dist
                                closest_bone = bone
                        
                        if closest_bone:
                            vg = mesh_obj.vertex_groups.get(closest_bone.name)
                            if vg:
                                vg.add([v_idx], 1.0, 'ADD')
                
                print(f"  ✓ Manual vertex groups created: {len(mesh_obj.vertex_groups)} groups")
        except Exception as e:
            print(f"  DEBUG: Auto Weights fallback failed: {e}")
        
        # Ensure armature is in pose mode for proper deformation
        bpy.context.view_layer.objects.active = armature_obj
        bpy.ops.object.mode_set(mode='POSE')
        print(f"  DEBUG: Armature set to POSE mode for deformation")
        
        # Ensure mesh armature modifier is active
        for mod in mesh_obj.modifiers:
            if mod.type == 'ARMATURE' and mod.object == armature_obj:
                mod.show_viewport = True
                mod.show_render = True
                print(f"  DEBUG: Armature modifier activated for {mesh_obj.name}")
                break

    def import_bmt(self, filepath, ddj_files, auto_convert):
        """Import BMT materials - FIXED with DDJ file list"""
        print(f"\n🎨 Importing BMT: {filepath}")
        
        materials = []
        converted_textures = {}
        
        # Create DDJ lookup map: filename -> full path
        ddj_map = {}
        for ddj_path in ddj_files:
            filename = os.path.basename(ddj_path)
            ddj_map[filename.lower()] = ddj_path
        
        print(f"  Available DDJ files: {len(ddj_files)}")
        if len(ddj_map) > 0:
            print(f"  DEBUG: DDJ map keys: {list(ddj_map.keys())[:5]}")  # Show first 5
        
        with open(filepath, 'rb') as file:
            # Signature check
            sig = file.read(12).decode('utf-8', errors='replace')
            if sig != "JMXVBMT 0102":
                # Better error message
                if sig.startswith("JMXVDDJ"):
                    raise ValueError(f"❌ HATA: DDJ dosyasını BMT olarak seçtiniz! BMT File için .bmt dosyası seçin, DDJ'leri DDJ Files bölümüne ekleyin.")
                else:
                    raise ValueError(f"Invalid BMT signature: {sig}")
            
            # Material count
            mat_count = struct.unpack('I', file.read(4))[0]
            print(f"  Materials: {mat_count}")
            
            for i in range(mat_count):
                # Material name
                name_len = struct.unpack('I', file.read(4))[0]
                name = file.read(name_len).decode('utf-8', errors='replace')
                
                # Colors
                colors = [struct.unpack('4f', file.read(16)) for _ in range(4)]
                
                # Unknown float
                unk_float = struct.unpack('f', file.read(4))[0]
                
                # Flag
                flag = struct.unpack('I', file.read(4))[0]
                
                # Diffuse map path
                diff_len = struct.unpack('I', file.read(4))[0]
                diff_path = file.read(diff_len).decode('utf-8', errors='replace') if diff_len > 0 else ""
                
                # Debug: Show material texture reference
                if diff_path:
                    print(f"  Material '{name}' → Texture: {diff_path}")
                
                # Additional data - FIXED: read byte by byte
                struct.unpack('f', file.read(4))  # float
                struct.unpack('B', file.read(1))  # byte
                struct.unpack('B', file.read(1))  # byte
                struct.unpack('?', file.read(1))  # bool
                
                # Normal map (if flag set)
                if flag & (1 << 13):
                    norm_len = struct.unpack('I', file.read(4))[0]
                    file.read(norm_len)  # skip normal map path
                    struct.unpack('I', file.read(4))  # skip int
                
                # AUTO-CONVERT DDJ from file list
                texture_path = None
                if auto_convert and diff_path and PIL_AVAILABLE:
                    # Extract base name from material path
                    tex_name = os.path.basename(diff_path)
                    tex_base = os.path.splitext(tex_name)[0]
                    ddj_filename = tex_base + '.ddj'
                    ddj_lookup = ddj_filename.lower()
                    
                    print(f"    Looking for: {ddj_filename}")
                    
                    # Debug: Show available DDJ files
                    if ddj_lookup not in ddj_map and len(ddj_map) > 0:
                        print(f"    DEBUG: Available in list: {list(ddj_map.keys())}")
                    
                    # Search in DDJ file list (case-insensitive)
                    if ddj_lookup in ddj_map:
                        ddj_path = ddj_map[ddj_lookup]
                        print(f"    ✓ Found: {os.path.basename(ddj_path)}")
                        
                        if ddj_path not in converted_textures:
                            png_path = self.convert_ddj(ddj_path)
                            if png_path:
                                converted_textures[ddj_path] = png_path
                                texture_path = png_path
                        else:
                            texture_path = converted_textures[ddj_path]
                    else:
                        print(f"    ⚠️  Not found in DDJ list")
                
                # Create material
                mat = self.create_material(name, colors, texture_path)
                materials.append(mat)
                
                # DEBUG: Show which texture was applied
                if texture_path:
                    texture_name = os.path.basename(texture_path)
                    print(f"  ✓ Material: {name} → Texture: {texture_name}")
                else:
                    print(f"  ✓ Material: {name} → No texture")
        
        print(f"  ✓ Converted {len(converted_textures)} textures")
        return materials

    def convert_ddj(self, filepath):
        """Convert DDJ to PNG - FIXED"""
        if not PIL_AVAILABLE:
            return None
        
        print(f"    🖼️  Converting: {os.path.basename(filepath)}")
        
        try:
            with open(filepath, 'rb') as file:
                sig = file.read(12).decode('utf-8', errors='replace')
                if sig != "JMXVDDJ 1000":
                    return None
                
                size = struct.unpack('I', file.read(4))[0]
                type_val = struct.unpack('I', file.read(4))[0]
                buffer = file.read(size)
                
                img = Image.open(io.BytesIO(buffer))
                png_path = filepath.replace('.ddj', '.png')
                img.save(png_path)
                
                print(f"    ✓ Saved: {os.path.basename(png_path)}")
                return png_path
                
        except Exception as e:
            print(f"    ✗ Failed: {e}")
            return None

    def create_material(self, name, colors, texture_path):
        """Create Blender material - FIXED"""
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        
        # Clear default nodes
        nodes.clear()
        
        # Create output node
        output = nodes.new('ShaderNodeOutputMaterial')
        output.location = (400, 0)
        
        # Create BSDF node
        bsdf = nodes.new('ShaderNodeBsdfPrincipled')
        bsdf.location = (0, 0)
        
        # Link BSDF to output
        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
        
        # Set base color
        if colors:
            bsdf.inputs['Base Color'].default_value = colors[0]
        
        # Add texture if available
        if texture_path and os.path.exists(texture_path):
            tex = nodes.new('ShaderNodeTexImage')
            tex.location = (-400, 0)
            
            try:
                tex.image = bpy.data.images.load(texture_path)
                links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
                print(f"      ✓ Texture applied")
            except Exception as e:
                print(f"      ✗ Texture load failed: {e}")
        
        return mat

    def apply_materials_to_obj(self, obj, materials):
        """Apply materials to object - FIXED: Match by material_name"""
        obj.data.materials.clear()
        
        # Get mesh's material name from custom property (set during creation)
        mesh_mat_name = obj.get('material_name', '')
        
        if mesh_mat_name:
            # Find matching material by name
            matched_mat = None
            for mat in materials:
                # Exact match or starts with (for duplicates like .001)
                if mat.name == mesh_mat_name or mat.name.startswith(mesh_mat_name + "."):
                    matched_mat = mat
                    break
            
            if matched_mat:
                obj.data.materials.append(matched_mat)
                obj.active_material = matched_mat
                
                # DEBUG: Show which texture is being applied
                if matched_mat.node_tree and matched_mat.node_tree.nodes.get("Image Texture"):
                    texture_node = matched_mat.node_tree.nodes["Image Texture"]
                    if texture_node.image:
                        texture_name = texture_node.image.name
                        print(f"    ✓ '{obj.name}' → Material: '{matched_mat.name}' → Texture: '{texture_name}'")
                    else:
                        print(f"    ✓ '{obj.name}' → Material: '{matched_mat.name}' → No texture")
                else:
                    print(f"    ✓ '{obj.name}' → Material: '{matched_mat.name}' → No texture node")
            else:
                # Fallback: use first material
                if materials:
                    obj.data.materials.append(materials[0])
                    obj.active_material = materials[0]
                    print(f"    ⚠️  '{obj.name}' → Fallback to '{materials[0].name}' ('{mesh_mat_name}' not found)")
        else:
            # No material name specified, use first material only
            if materials:
                obj.data.materials.append(materials[0])
                obj.active_material = materials[0]

# ============================================================================
# UI Sınıfları
# ============================================================================
class FILE_UL_List(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.path:
            layout.label(text=os.path.basename(item.path), icon='FILE')
        else:
            layout.label(text="<Empty>", icon='ERROR')

class FILE_OT_AddFile(Operator):
    bl_idname = "file_list.add_file"
    bl_label = "Add BMS File(s)"
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.bms", options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH')
    
    def execute(self, context):
        settings = context.scene.game_importer_settings
        
        # Support multiple file selection
        if self.files:
            for file in self.files:
                item = settings.bms_files.add()
                item.path = os.path.join(self.directory, file.name)
        else:
            item = settings.bms_files.add()
            item.path = self.filepath
        
        return {'FINISHED'}
    
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class DDJ_OT_AddFile(Operator):
    bl_idname = "ddj_list.add_file"
    bl_label = "Add DDJ File"
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.ddj", options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH')
    
    def execute(self, context):
        settings = context.scene.game_importer_settings
        
        # Support multiple file selection
        if self.files:
            for file in self.files:
                item = settings.ddj_files.add()
                item.path = os.path.join(self.directory, file.name)
        else:
            item = settings.ddj_files.add()
            item.path = self.filepath
        
        return {'FINISHED'}
    
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class FILE_OT_RemoveFile(Operator):
    bl_idname = "file_list.remove_file"
    bl_label = "Remove File"
    
    @classmethod
    def poll(cls, context):
        return len(context.scene.game_importer_settings.bms_files) > 0
    
    def execute(self, context):
        settings = context.scene.game_importer_settings
        if settings.bms_files:
            settings.bms_files.remove(settings.active_bms_index)
            if settings.bms_files:
                settings.active_bms_index = min(settings.active_bms_index, len(settings.bms_files)-1)
        return {'FINISHED'}

class FILE_OT_ClearFiles(Operator):
    bl_idname = "file_list.clear_files"
    bl_label = "Clear All"
    
    @classmethod
    def poll(cls, context):
        return len(context.scene.game_importer_settings.bms_files) > 0
    
    def execute(self, context):
        context.scene.game_importer_settings.bms_files.clear()
        return {'FINISHED'}

class DDJ_OT_RemoveFile(Operator):
    bl_idname = "ddj_list.remove_file"
    bl_label = "Remove DDJ File"
    
    @classmethod
    def poll(cls, context):
        return len(context.scene.game_importer_settings.ddj_files) > 0
    
    def execute(self, context):
        settings = context.scene.game_importer_settings
        if settings.ddj_files:
            settings.ddj_files.remove(settings.active_ddj_index)
            if settings.ddj_files:
                settings.active_ddj_index = min(settings.active_ddj_index, len(settings.ddj_files)-1)
        return {'FINISHED'}

class DDJ_OT_ClearFiles(Operator):
    bl_idname = "ddj_list.clear_files"
    bl_label = "Clear All DDJ"
    
    @classmethod
    def poll(cls, context):
        return len(context.scene.game_importer_settings.ddj_files) > 0
    
    def execute(self, context):
        context.scene.game_importer_settings.ddj_files.clear()
        return {'FINISHED'}

class GameImporterPanel(Panel):
    bl_label = "Game Importer V11 FIXED"
    bl_idname = "VIEW3D_PT_game_importer_v11_fixed"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Game Import'
    
    def draw(self, context):
        layout = self.layout
        s = context.scene.game_importer_settings
        
        # BMS Files
        box = layout.box()
        box.label(text="BMS Files (Meshes):", icon='MESH_DATA')
        row = box.row()
        row.template_list("FILE_UL_List", "", s, "bms_files", s, "active_bms_index", rows=3)
        col = row.column(align=True)
        col.operator("file_list.add_file", icon='ADD', text="")
        col.operator("file_list.remove_file", icon='REMOVE', text="")
        col.operator("file_list.clear_files", icon='X', text="")
        box.prop(s, "combine_meshes")
        
        # BSK File
        box = layout.box()
        box.label(text="BSK File (Skeleton):", icon='ARMATURE_DATA')
        box.prop(s, "bsk_file", text="")
        row = box.row(align=True)
        row.prop(s, "import_skeleton", toggle=True)
        row.prop(s, "bind_mesh", text="Bind", toggle=True)
        box.prop(s, "split_armatures", text="Split skeleton chains into separate armatures")
        if s.split_armatures:
            box.prop(s, "split_root_children", text="If single root: split by children")
        
        # BMT File
        box = layout.box()
        box.label(text="BMT File (Materials):", icon='MATERIAL')
        box.prop(s, "bmt_file", text="")
        row = box.row(align=True)
        row.prop(s, "apply_materials", toggle=True)
        row.prop(s, "auto_convert_ddj", text="Auto DDJ", toggle=True)
        
        # DDJ Files (Textures)
        box = layout.box()
        box.label(text="DDJ Files (Textures):", icon='TEXTURE')
        row = box.row()
        row.template_list("FILE_UL_List", "ddj", s, "ddj_files", s, "active_ddj_index", rows=3)
        col = row.column(align=True)
        col.operator("ddj_list.add_file", icon='ADD', text="")
        col.operator("ddj_list.remove_file", icon='REMOVE', text="")
        col.operator("ddj_list.clear_files", icon='X', text="")
        
        if not PIL_AVAILABLE:
            box.label(text="⚠️ Pillow (PIL) not installed!", icon='ERROR')
        
        # Import Button
        layout.separator()
        row = layout.row()
        row.scale_y = 2.0
        row.operator("import_scene.game_files", icon='IMPORT')
        
        # Status
        layout.separator()
        box = layout.box()
        box.label(text="Status:", icon='INFO')
        box.label(text=f"BMS: {len(s.bms_files)} files")
        box.label(text=f"DDJ: {len(s.ddj_files)} files")
        box.label(text=f"BSK: {'✓' if s.bsk_file else '✗'}")
        box.label(text=f"BMT: {'✓' if s.bmt_file else '✗'}")

# ============================================================================
# Registration
# ============================================================================
classes = (
    FileListItem,
    GameImporterSettings,
    GameImporter,
    FILE_UL_List,
    FILE_OT_AddFile,
    FILE_OT_RemoveFile,
    FILE_OT_ClearFiles,
    DDJ_OT_AddFile,
    DDJ_OT_RemoveFile,
    DDJ_OT_ClearFiles,
    GameImporterPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.game_importer_settings = PointerProperty(type=GameImporterSettings)
    print("✓ Game Importer V11 FIXED v4.5.1 DEBUG registered")

def unregister():
    del bpy.types.Scene.game_importer_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    print("✗ Game Importer V11 FIXED v4.5.1 DEBUG unregistered")

if __name__ == "__main__":
    register()

