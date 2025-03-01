from binary_reader import BinaryReader
import bstruct
import crc
import itertools
import os
import re
import subprocess
import struct
import types

# If need be, set the following to your FXC path.
# On Mac/Linux, it should be a script that either calls FXC under Wine or marshals the blob to a remote machine and then calls FXC on it.
FXC = 'fxc'

crc_config = crc.Configuration(width=32, polynomial=0x04C11DB7, init_value=0, final_xor_value=0, reverse_input=True, reverse_output=True)
crc_calc = crc.Calculator(crc_config, optimized=True)

digits = re.compile(r'\d+')
resource_binding_size = re.compile(r'\s(\w+)(?:\[\d+\])?;\s*//\s*Offset:\s*0\s*Size:\s*(\d+)$', re.MULTILINE)

def reverse_bits_u32(x):
    x = ((x & 0x55555555) << 1) | ((x & 0xAAAAAAAA) >> 1)
    x = ((x & 0x33333333) << 2) | ((x & 0xCCCCCCCC) >> 2)
    x = ((x & 0x0F0F0F0F) << 4) | ((x & 0xF0F0F0F0) >> 4)
    x = ((x & 0x00FF00FF) << 8) | ((x & 0xFF00FF00) >> 8)
    x = ((x & 0x0000FFFF) << 16) | ((x & 0xFFFF0000) >> 16)
    return x

def crc32(s: bytes) -> int:
    return crc_calc.checksum(s)

def crc32_prefixed_calc(prefix_crc32: int, optimized: bool = False) -> crc.Calculator:
    seeded_config = crc.Configuration(width=32, polynomial=0x04C11DB7, init_value=reverse_bits_u32(prefix_crc32), final_xor_value=0, reverse_input=True, reverse_output=True)
    return crc.Calculator(seeded_config, optimized=optimized)

file_header_struct = bstruct.BStruct((
    (bstruct.bytes(4), 'magic'),             # 0
    (bstruct.uint32, 'version'),             # 1
    (bstruct.bytes(4), 'graphics_platform'), # 2
    (bstruct.uint32, 'file_size'),           # 3
    (bstruct.uint32, 'blobs_offset'),        # 4
    (bstruct.uint32, 'strings_offset'),      # 5
    (bstruct.uint32, 'vertex_shader_count'), # 6
    (bstruct.uint32, 'pixel_shader_count'),  # 7
    (bstruct.uint32, 'mat_params_size'),     # 8
    (bstruct.uint16, 'mat_param_count'),     # 9
    (bstruct.uint16, 'has_mat_param_defaults'),
    (bstruct.uint16, 'constant_count'),      # 10 (A)
    (bstruct.uint16, 'unk1'),
    (bstruct.uint16, 'sampler_count'),       # 11 (B)
    (bstruct.uint16, 'texture_count'),
    (bstruct.uint16, 'uav_count'),           # 12 (C)
    (bstruct.uint16, 'unk2'),
    (bstruct.uint32, 'system_key_count'),    # 13 (D)
    (bstruct.uint32, 'scene_key_count'),     # 14 (E)
    (bstruct.uint32, 'mat_key_count'),       # 15 (F)
    (bstruct.uint32, 'node_count'),          # 16 (10)
    (bstruct.uint32, 'item_count'),          # 17 (11)
))

class FileHeader:
    def __init__(self, data: dict) -> None:
        self.__dict__.update(data)

    @property
    def is_7(self) -> bool:
        return self.has_mat_param_defaults != 0 or self.texture_count != 0

    @staticmethod
    def read(reader: BinaryReader) -> any:
        data = file_header_struct.read(reader)
        return FileHeader(data)

    def write(self, writer: BinaryReader) -> None:
        file_header_struct.write(writer, self.__dict__)

resource_struct = bstruct.BStruct((
    (bstruct.uint32, 'id'),
    (bstruct.uint32, 'name_offset'),
    (bstruct.uint16, 'name_size'),
    (bstruct.uint16, 'unk'), # Observed to always be 0 for constants and samplers, and 1 for textures
    (bstruct.uint16, 'slot'),
    (bstruct.uint16, 'size'),
))

resource_types = types.SimpleNamespace()
resource_types.RESOURCE_CBUFFER = 'c'
resource_types.RESOURCE_SAMPLER = 's'
resource_types.RESOURCE_TEXTURE = 't'
resource_types.RESOURCE_UAV = 'u'

class Resource:
    def __init__(self, data: dict, name: bytes) -> None:
        self.__dict__.update(data)
        self.name = name

    @staticmethod
    def read(reader: BinaryReader, strings: bytes) -> any:
        data = resource_struct.read(reader)
        name = strings[data['name_offset']:(data['name_offset'] + data['name_size'])]
        return Resource(data, name)

    def update(self, shpk: any) -> None:
        self.name_offset = shpk.find_or_add_string(self.name)
        self.name_size = len(self.name)

    def write(self, writer: BinaryReader) -> None:
        resource_struct.write(writer, self.__dict__)

class HasResources:
    def get_constant_by_id(self, constant_id: int) -> Resource:
        for constant in self.constants:
            if constant.id == constant_id:
                return constant
        return None

    def has_constant_id(self, constant_id: int) -> bool:
        return self.get_constant_by_id(constant_id) is not None

    def get_constant_by_name(self, constant_name: bytes) -> Resource:
        for constant in self.constants:
            if constant.name == constant_name:
                return constant
        return None

    def has_constant_name(self, constant_name: bytes) -> bool:
        return self.get_constant_by_name(constant_name) is not None

    def get_sampler_by_id(self, sampler_id: int) -> Resource:
        for sampler in self.samplers:
            if sampler.id == sampler_id:
                return sampler
        return None

    def has_sampler_id(self, sampler_id: int) -> bool:
        return self.get_sampler_by_id(sampler_id) is not None

    def get_sampler_by_name(self, sampler_name: bytes) -> Resource:
        for sampler in self.samplers:
            if sampler.name == sampler_name:
                return sampler
        return None

    def has_sampler_name(self, sampler_name: bytes) -> bool:
        return self.get_sampler_by_name(sampler_name) is not None

    def get_texture_by_id(self, texture_id: int) -> Resource:
        for texture in self.textures:
            if texture.id == texture_id:
                return texture
        return None

    def has_texture_id(self, texture_id: int) -> bool:
        return self.get_texture_by_id(texture_id) is not None

    def get_texture_by_name(self, texture_name: bytes) -> Resource:
        for texture in self.textures:
            if texture.name == texture_name:
                return texture
        return None

    def has_texture_name(self, texture_name: bytes) -> bool:
        return self.get_texture_by_name(texture_name) is not None

    def get_uav_by_id(self, uav_id: int) -> Resource:
        for uav in self.uavs:
            if uav.id == uav_id:
                return uav
        return None

    def has_uav_id(self, uav_id: int) -> bool:
        return self.get_uav_by_id(uav_id) is not None

    def get_uav_by_name(self, uav_name: bytes) -> Resource:
        for uav in self.uavs:
            if uav.name == uav_name:
                return uav
        return None

    def has_uav_name(self, uav_name: bytes) -> bool:
        return self.get_uav_by_name(uav_name) is not None

def strip_ext(name):
    pos = name.rfind('.')
    return name if pos < 0 else name[:pos]

def add_section(sections: dict, name: str, section: list[str]) -> None:
    while len(section) > 0 and len(section[0]) <= 3:
        section = section[1:]
    while len(section) > 0 and len(section[-1]) <= 3:
        section = section[:-1]
    sections[name] = [line[3:] for line in section]

def parse_header(lines: list[str]) -> dict:
    sections = {}
    last_name = ''
    last_start = 0
    for i in range(1, len(lines) - 1):
        if len(lines[i - 1]) <= 3 and len(lines[i + 1]) <= 3:
            current = lines[i].rstrip()
            if current.endswith(':'):
                add_section(sections, last_name, lines[last_start:(i - 1)])
                last_name = current[3:-1]
                last_start = i + 2
    add_section(sections, last_name, lines[last_start:])
    return sections

def parse_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    columns = []
    dashes = lines[1]
    i = 0
    while True:
        start = dashes.find('-', i)
        if start < 0:
            break
        end = dashes.find(' ', start + 1)
        if end < 0:
            columns.append((start, len(dashes)))
            break
        else:
            columns.append((start, end))
            i = end + 1
    headers = [lines[0][start:end].strip() for (start, end) in columns]
    data = [[line[start:end].strip() for (start, end) in columns] for line in lines[2:]]
    return (headers, data)

def extract_int(s: str) -> int:
    return int(s[digits.search(s).start():])

INPUT_FLAGS = {
    'POSITION': 1 << 0,
    'BLENDWEIGHT': 1 << 1,
    'NORMAL': 1 << 2,
    'COLOR0': 1 << 3,
    'COLOR1': 1 << 4,
    'FOG': 1 << 5,
    'PSIZE': 1 << 6,
    'BLENDINDICES': 1 << 7,
    'TEXCOORD0': 1 << 8,
    'TEXCOORD1': 1 << 9,
    'TEXCOORD2': 1 << 10,
    'TEXCOORD3': 1 << 11,
    'TEXCOORD4': 1 << 12,
    'TEXCOORD5': 1 << 13,
    'TANGENT': 1 << 14,
    'BINORMAL': 1 << 15,
    'DEPTH': 1 << 16,
}

def parse_resource_bindings(fxc_dumpbin_output: str, graphics_platform: bytes, expected_shader_stage: str) -> any:
    lines = fxc_dumpbin_output.split('\n')
    instructions = [line for line in lines if not line.startswith('//') and len(line) > 0]
    shader_model = instructions[0].split('_')
    shader_model_major = int(shader_model[1])
    expected_shader_model = 5 if graphics_platform == b'DX11' else 3
    if shader_model[0][0].lower() != expected_shader_stage or shader_model_major != expected_shader_model:
        raise ValueError("Expected %ss_%d_* shader, got %s" % (expected_shader_stage, expected_shader_model, instructions[0]))
    header = parse_header(lines[:lines.index(instructions[0])])
    resources = []
    inputs = None
    match shader_model_major:
        case 3:
            regs = header.get('Registers')
            if regs is not None:
                (_, registers) = parse_table(regs)
                for register in registers:
                    resource_type = register[1][0].lower()
                    if resource_type == resource_types.RESOURCE_SAMPLER:
                        resource_type = resource_types.RESOURCE_TEXTURE
                    resources.append({
                        'name': register[0],
                        'type': resource_type,
                        'slot': int(register[1][1:]),
                        'size': int(register[2]),
                    })
            # TODO parse input signature
            # inputs = {'declared': ???}
        case 5:
            binds = header.get('Resource Bindings')
            if binds is not None:
                (_, bindings) = parse_table(binds)
                for binding in bindings:
                    resource_type = binding[1][0].lower()
                    resources.append({
                        'name': binding[0],
                        'type': resource_type,
                        'slot': extract_int(binding[4]),
                        'size': 1 if resource_type == resource_types.RESOURCE_TEXTURE else 0,
                    })
            buffers = header.get('Buffer Definitions')
            if buffers is not None:
                buffers = '\n'.join(buffers)
                for match in resource_binding_size.finditer(buffers):
                    (name, size) = match.groups()
                    for resource in resources:
                        if resource['type'] == resource_types.RESOURCE_CBUFFER and resource['name'] == name:
                            resource['size'] = (int(size) + 0xF) >> 4
            isig = header.get('Input signature')
            if isig is not None:
                (_, ins) = parse_table(isig)
                inputs = {'declared': 0, 'used': 0}
                for input in ins:
                    if input[0] in INPUT_FLAGS:
                        flag = INPUT_FLAGS[input[0]]
                    elif (input[0] + input[1]) in INPUT_FLAGS:
                        flag = INPUT_FLAGS[input[0] + input[1]]
                    else:
                        flag = 0
                    inputs['declared'] |= flag
                    if len(input[6]) > 0:
                        inputs['used'] |= flag
        case _:
            raise NotImplementedError()
    return (resources, inputs)

def normalize_resource_name(name: str) -> str:
    dot = name.find('.')
    if dot >= 0:
        return name[:dot]
    elif name.endswith('_S') or name.endswith('_T'):
        return name[:-2]
    else:
        return name

shader_struct = bstruct.BStruct((
    (bstruct.uint32, 'offset'),
    (bstruct.uint32, 'size'),
    (bstruct.uint16, 'constant_count'),
    (bstruct.uint16, 'sampler_count'),
    (bstruct.uint16, 'uav_count'),
    (bstruct.uint16, 'texture_count'),
))

stages = types.SimpleNamespace()
stages.STAGE_VERTEX = 'v'
stages.STAGE_PIXEL = 'p'

class Shader(HasResources):
    def __init__(self, data: dict, stage: int, constants: list[Resource], samplers: list[Resource], textures: list[Resource], uavs: list[Resource], extra_header: bytes, blob: bytes) -> None:
        self.__dict__.update(data)
        self.stage = stage
        self.constants = constants
        self.samplers = samplers
        self.textures = textures
        self.uavs = uavs
        self.extra_header = extra_header
        self.blob = blob

    @staticmethod
    def read(reader: BinaryReader, stage: int, graphics_platform: bytes, blobs: bytes, strings: bytes) -> any:
        data = shader_struct.read(reader)
        constants = []
        for _ in range(data['constant_count']):
            constants.append(Resource.read(reader, strings))
        samplers = []
        for _ in range(data['sampler_count']):
            samplers.append(Resource.read(reader, strings))
        uavs = []
        for _ in range(data['uav_count']):
            uavs.append(Resource.read(reader, strings))
        textures = []
        for _ in range(data['texture_count']):
            textures.append(Resource.read(reader, strings))
        blob = blobs[data['offset']:(data['offset'] + data['size'])]
        if stage == stages.STAGE_VERTEX:
            extra_header_size = 8 if graphics_platform == b'DX11' else 4
            extra_header = blob[0:extra_header_size]
            blob = blob[extra_header_size:]
        else:
            extra_header = b''
        return Shader(data, stage, constants, samplers, textures, uavs, extra_header, blob)

    def new_variant(self):
        return Shader({
            'constant_count': 0,
            'sampler_count': 0,
            'uav_count': 0,
            'offset': 0,
            'size': 0,
            'padding': 0,
        }, self.stage, [], [], [], [], self.extra_header, b"")

    def update(self, shpk: any) -> None:
        self.offset = len(shpk.blobs)
        self.size = len(self.extra_header) + len(self.blob)
        shpk.blobs += self.extra_header + self.blob
        for constant in self.constants:
            constant.update(shpk)
        for sampler in self.samplers:
            sampler.update(shpk)
        for texture in self.textures:
            texture.update(shpk)
        for uav in self.uavs:
            uav.update(shpk)

    def update_resources(self, shpk: any, new_shader_path: str, use_pre_disasm: bool) -> None:
        if use_pre_disasm and os.path.isfile(strip_ext(new_shader_path) + '.S'):
            with open(strip_ext(new_shader_path) + '.S', 'rt') as f:
                raw_disasm = f.read()
        else:
            raw_disasm = subprocess.run([FXC, '/nologo', '/dumpbin', new_shader_path], capture_output=True, text=True, check=True).stdout
        (bindings, inputs) = parse_resource_bindings(raw_disasm, shpk.file_header.graphics_platform, self.stage)
        if shpk.file_header.graphics_platform == b'DX11' and not shpk.is_7:
            samplers = {}
            textures = {}
            for binding in bindings:
                match binding['type']:
                    case resource_types.RESOURCE_TEXTURE:
                        textures[binding['slot']] = normalize_resource_name(binding['name'])
                    case resource_types.RESOURCE_SAMPLER:
                        samplers[binding['slot']] = normalize_resource_name(binding['name'])
            if len(samplers) != len(textures) or not all((samplers[slot] == textures.get(slot) for slot in samplers)):
                raise ValueError("The supplied blob (%s) has inconsistent sampler and texture allocation." % (os.path.basename(new_shader_path),))
        if self.stage == stages.STAGE_VERTEX and inputs is not None:
            if shpk.file_header.graphics_platform == b'DX11':
                self.extra_header = struct.pack('<LL', inputs['declared'], inputs['used'])
            else:
                self.extra_header = struct.pack('<L', inputs['declared'])
        constants = []
        samplers = []
        textures = []
        uavs = []
        for binding in bindings:
            match binding['type']:
                case resource_types.RESOURCE_CBUFFER:
                    name = bytes(normalize_resource_name(binding['name']), 'utf-8')
                    existing = self.get_constant_by_name(name)
                    if existing is None:
                        existing = shpk.get_constant_by_name(name)
                    id = existing.id if existing is not None else crc32(name)
                    constants.append(Resource({
                        'id': id,
                        'name_offset': 0,
                        'name_size': 0,
                        'unk': 0,
                        'slot': binding['slot'],
                        'size': binding['size'],
                    }, name))
                case resource_types.RESOURCE_TEXTURE:
                    name = bytes(normalize_resource_name(binding['name']), 'utf-8')
                    existing = self.get_texture_by_name(name)
                    if existing is None:
                        existing = shpk.get_texture_by_name(name)
                    id = existing.id if existing is not None else crc32(name)
                    textures.append(Resource({
                        'id': id,
                        'name_offset': 0,
                        'name_size': 0,
                        'unk': 1,
                        'slot': binding['slot'],
                        'size': binding['slot'],
                    }, name))
                case resource_types.RESOURCE_SAMPLER:
                    name = bytes(normalize_resource_name(binding['name']), 'utf-8')
                    existing = self.get_sampler_by_name(name)
                    if existing is None:
                        existing = shpk.get_sampler_by_name(name)
                    id = existing.id if existing is not None else crc32(name)
                    samplers.append(Resource({
                        'id': id,
                        'name_offset': 0,
                        'name_size': 0,
                        'unk': 0,
                        'slot': binding['slot'],
                        'size': binding['slot'],
                    }, name))
                case resource_types.RESOURCE_UAV:
                    name = bytes(normalize_resource_name(binding['name']), 'utf-8')
                    existing = self.get_uav_by_name(name)
                    if existing is None:
                        existing = shpk.get_uav_by_name(name)
                    id = existing.id if existing is not None else crc32(name)
                    uavs.append(Resource({
                        'id': id,
                        'name_offset': 0,
                        'name_size': 0,
                        'unk': 0, # XXX this one was never observed
                        'slot': binding['slot'],
                        'size': binding['slot'],
                    }, name))
        self.constants = constants
        self.samplers = samplers
        self.textures = textures if shpk.is_7 else []
        self.uavs = uavs

    def write(self, writer: BinaryReader) -> None:
        self.constant_count = len(self.constants)
        self.sampler_count = len(self.samplers)
        self.texture_count = len(self.textures)
        self.uav_count = len(self.uavs)
        shader_struct.write(writer, self.__dict__)
        for constant in self.constants:
            constant.write(writer)
        for sampler in self.samplers:
            sampler.write(writer)
        for uav in self.uavs:
            uav.write(writer)
        for texture in self.textures:
            texture.write(writer)

mat_param_struct = bstruct.BStruct((
    (bstruct.uint32, 'id'),
    (bstruct.uint16, 'offset'),
    (bstruct.uint16, 'size'),
))

class MatParam:
    def __init__(self, data: dict) -> None:
        self.__dict__.update(data)

    @staticmethod
    def read(reader: BinaryReader) -> any:
        return MatParam(mat_param_struct.read(reader))

    def write(self, writer: BinaryReader) -> None:
        mat_param_struct.write(writer, self.__dict__)

node_struct = bstruct.BStruct((
    (bstruct.uint32, 'id'),
    (bstruct.uint32, 'pass_count'),
    (bstruct.bytes(16), 'pass_indices'),
))

def pseudoset_add(pseudoset: list, value) -> None:
    if value not in pseudoset:
        pseudoset.append(value)

class Node:
    def __init__(self, data: dict, system_keys: list[int], scene_keys: list[int], mat_keys: list[int], sub_view_keys: tuple[int], passes: list[tuple[int]]) -> None:
        self.__dict__.update(data)
        self.system_keys = system_keys
        self.scene_keys = scene_keys
        self.mat_keys = mat_keys
        self.sub_view_keys = sub_view_keys
        self.passes = passes

    @staticmethod
    def read(reader: BinaryReader, file_header: FileHeader, global_system_keys, global_scene_keys, global_mat_keys, global_sub_view_keys) -> any:
        data = node_struct.read(reader)
        system_keys = []
        for i in range(file_header.system_key_count):
            kv = reader.read_uint32()
            pseudoset_add(global_system_keys[i][2], kv)
            system_keys.append(kv)
        scene_keys = []
        for i in range(file_header.scene_key_count):
            kv = reader.read_uint32()
            pseudoset_add(global_scene_keys[i][2], kv)
            scene_keys.append(kv)
        mat_keys = []
        for i in range(file_header.mat_key_count):
            kv = reader.read_uint32()
            pseudoset_add(global_mat_keys[i][2], kv)
            mat_keys.append(kv)
        (kv0, kv1) = reader.read_uint32(2)
        pseudoset_add(global_sub_view_keys[0][2], kv0)
        pseudoset_add(global_sub_view_keys[1][2], kv1)
        sub_view_keys = (kv0, kv1)
        passes = []
        for _ in range(data['pass_count']):
            passes.append(reader.read_uint32(3))
        return Node(data, system_keys, scene_keys, mat_keys, sub_view_keys, passes)

    def new_variant(self, id: int):
        return Node({
            'id': id,
            'pass_count': self.pass_count,
            'pass_indices': self.pass_indices,
        }, self.system_keys.copy(), self.scene_keys.copy(), self.mat_keys.copy(), self.sub_view_keys, self.passes.copy())

    def update(self, shpk: any) -> None:
        self.pass_count = len(self.passes)

    def write(self, writer: BinaryReader) -> None:
        node_struct.write(writer, self.__dict__)
        for system_key in self.system_keys:
            writer.write_uint32(system_key)
        for scene_key in self.scene_keys:
            writer.write_uint32(scene_key)
        for mat_key in self.mat_keys:
            writer.write_uint32(mat_key)
        writer.write_uint32(self.sub_view_keys)
        for pass_ in self.passes:
            writer.write_uint32(pass_)

def collect_sh_resources(resources: dict, sh_resources: list[Resource], get_existing: any, resource_type: int) -> None:
    for resource in sh_resources:
        carry = resources.get(resource.id)
        if carry is not None and resource_type != resource_types.RESOURCE_CBUFFER:
            continue
        existing = get_existing(resource.id)
        resources[resource.id] = Resource({
            'id': resource.id,
            'unk': existing.unk if existing is not None else resource.unk,
            'slot': existing.slot if existing is not None else (65535 if resource_type == resource_types.RESOURCE_CBUFFER else 2),
            'size': max(carry.size if carry is not None else 0, resource.size) if resource_type == resource_types.RESOURCE_CBUFFER else (existing.size if existing is not None else 0),
        }, resource.name)

class ShPk(HasResources):
    def __init__(self, file_header: FileHeader, strings: bytes, vertex_shaders: list[Shader], pixel_shaders: list[Shader], mat_params: list[MatParam], mat_param_defaults: list[float], constants: list[Resource], samplers: list[Resource], textures: list[Resource], uavs: list[Resource], system_keys: list[tuple[int]], scene_keys: list[tuple[int]], mat_keys: list[tuple[int]], sub_view_keys: tuple[tuple[int]], nodes: list[Node], items: list[tuple[int]], rest: bytes) -> None:
        self.file_header = file_header
        self.strings = strings
        self.vertex_shaders = vertex_shaders
        self.pixel_shaders = pixel_shaders
        self.mat_params = mat_params
        self.mat_param_defaults = mat_param_defaults
        self.constants = constants
        self.samplers = samplers
        self.textures = textures
        self.uavs = uavs
        self.system_keys = system_keys
        self.scene_keys = scene_keys
        self.mat_keys = mat_keys
        self.sub_view_keys = sub_view_keys
        self.nodes = nodes
        self.items = items
        self.rest = rest

    @property
    def is_7(self) -> bool:
        return self.file_header.is_7

    @property
    def all_shaders(self):
        return itertools.chain(self.vertex_shaders, self.pixel_shaders)

    def get_shader(self, stage: int, index: int) -> Shader:
        match stage:
            case stages.STAGE_VERTEX:
                return self.vertex_shaders[index]
            case stages.STAGE_PIXEL:
                return self.pixel_shaders[index]
            case _:
                raise ValueError()

    def get_mat_param_by_id(self, mat_param_id: int) -> MatParam:
        for mat_param in self.mat_params:
            if mat_param.id == mat_param_id:
                return mat_param
        return None

    def has_mat_param_id(self, mat_param_id: int) -> bool:
        return self.get_mat_param_by_id(mat_param_id) is not None

    @staticmethod
    def read(reader: BinaryReader) -> any:
        file_header = FileHeader.read(reader)
        blobs = bytes(reader.buffer()[file_header.blobs_offset:file_header.strings_offset])
        strings = bytes(reader.buffer()[file_header.strings_offset:])
        vertex_shaders = []
        for _ in range(file_header.vertex_shader_count):
            vertex_shaders.append(Shader.read(reader, stages.STAGE_VERTEX, file_header.graphics_platform, blobs, strings))
        pixel_shaders = []
        for _ in range(file_header.pixel_shader_count):
            pixel_shaders.append(Shader.read(reader, stages.STAGE_PIXEL, file_header.graphics_platform, blobs, strings))
        mat_params = []
        for _ in range(file_header.mat_param_count):
            mat_params.append(MatParam.read(reader))
        mat_param_defaults = reader.read_float(file_header.mat_params_size >> 2) if file_header.has_mat_param_defaults != 0 else None
        constants = []
        for _ in range(file_header.constant_count):
            constants.append(Resource.read(reader, strings))
        samplers = []
        for _ in range(file_header.sampler_count):
            samplers.append(Resource.read(reader, strings))
        textures = []
        for _ in range(file_header.texture_count):
            textures.append(Resource.read(reader, strings))
        uavs = []
        for _ in range(file_header.uav_count):
            uavs.append(Resource.read(reader, strings))
        system_keys = []
        for _ in range(file_header.system_key_count):
            (sk, dv) = reader.read_uint32(2)
            system_keys.append((sk, dv, [dv]))
        scene_keys = []
        for _ in range(file_header.scene_key_count):
            (sk, dv) = reader.read_uint32(2)
            scene_keys.append((sk, dv, [dv]))
        mat_keys = []
        for _ in range(file_header.mat_key_count):
            (sk, dv) = reader.read_uint32(2)
            mat_keys.append((sk, dv, [dv]))
        (dv0, dv1) = reader.read_uint32(2)
        sub_view_keys = ((0, dv0, [dv0]), (1, dv1, [dv1]))
        nodes = []
        for _ in range(file_header.node_count):
            nodes.append(Node.read(reader, file_header, system_keys, scene_keys, mat_keys, sub_view_keys))
        items = []
        for _ in range(file_header.item_count):
            items.append(reader.read_uint32(2))
        rest = reader.read_bytes(file_header.blobs_offset - reader.pos())
        return ShPk(file_header, strings, vertex_shaders, pixel_shaders, mat_params, mat_param_defaults, constants, samplers, textures, uavs, system_keys, scene_keys, mat_keys, sub_view_keys, nodes, items, rest)

    def find_or_add_string(self, string: bytes) -> int:
        pos = (b'\0' + self.strings).find(b'\0' + string)
        if pos == -1:
            pos = len(self.strings)
            self.strings += string + b'\0'
        return pos

    def add_mat_key(self, key: int, default_value: int, replacements: dict) -> None:
        key_i = len(self.mat_keys)
        multiplier = (31 ** (key_i + 2)) & 0xFFFFFFFF
        values = [default_value]
        node_replacements = {}
        new_items = {}
        for value in replacements:
            pseudoset_add(values, value)
            node_replacements[value] = {}
            new_items[value] = []
        self.mat_keys.append((key, default_value, values))
        for i in range(len(self.nodes)):
            node = self.nodes[i]
            id = node.id
            node.id = (id + default_value * multiplier) & 0xFFFFFFFF
            node.mat_keys.append(default_value)
            for value in replacements:
                (vs_replacements, ps_replacements) = replacements[value]
                if any((vs in vs_replacements or ps in ps_replacements for (_, vs, ps) in node.passes)):
                    new_i = len(self.nodes)
                    new_node = node.new_variant((id + value * multiplier) & 0xFFFFFFFF)
                    new_node.mat_keys[key_i] = value
                    for j in range(len(new_node.passes)):
                        (p_id, vs, ps) = new_node.passes[j]
                        new_node.passes[j] = (p_id, vs_replacements.get(vs, vs), ps_replacements.get(ps, ps))
                    self.nodes.append(new_node)
                    node_replacements[value][i] = new_i
                else:
                    new_items[value].append(((id + value * multiplier) & 0xFFFFFFFF, i))
        for i in range(len(self.items)):
            (id, node_index) = self.items[i]
            self.items[i] = ((id + default_value * multiplier) & 0xFFFFFFFF, node_index)
            for value in replacements:
                new_items[value].append(((id + value * multiplier) & 0xFFFFFFFF, node_replacements[value].get(node_index, node_index)))
        for value in new_items:
            self.items.extend(new_items[value])

    def update(self) -> None:
        nodes = {}
        for node in self.nodes:
            if node.id in nodes:
                raise ValueError()
            nodes[node.id] = node
        for (id, node_i) in self.items:
            if id in nodes:
                raise ValueError()
            nodes[id] = self.nodes[node_i]
        self.blobs = b''
        self.strings = b''
        for shader in self.vertex_shaders:
            shader.update(self)
        for shader in self.pixel_shaders:
            shader.update(self)
        for constant in self.constants:
            constant.update(self)
        for sampler in self.samplers:
            sampler.update(self)
        for texture in self.textures:
            texture.update(self)
        for uav in self.uavs:
            uav.update(self)
        for node in self.nodes:
            node.update(self)
        self.update_mat_param_defaults()
        self.file_header.vertex_shader_count = len(self.vertex_shaders)
        self.file_header.pixel_shader_count = len(self.pixel_shaders)
        self.file_header.mat_param_count = len(self.mat_params)
        self.file_header.constant_count = len(self.constants)
        self.file_header.sampler_count = len(self.samplers)
        self.file_header.texture_count = len(self.textures)
        self.file_header.uav_count = len(self.uavs)
        self.file_header.system_key_count = len(self.system_keys)
        self.file_header.scene_key_count = len(self.scene_keys)
        self.file_header.mat_key_count = len(self.mat_keys)
        self.file_header.node_count = len(self.nodes)
        self.file_header.item_count = len(self.items)
        dummy_writer = BinaryReader()
        self.unsafe_write_header(dummy_writer)
        self.file_header.blobs_offset = dummy_writer.size()
        self.file_header.strings_offset = self.file_header.blobs_offset + len(self.blobs)
        self.file_header.file_size = self.file_header.strings_offset + len(self.strings)

    def update_resources(self) -> None:
        constants = {}
        samplers = {}
        textures = {}
        uavs = {}
        for shader in self.vertex_shaders:
            collect_sh_resources(constants, shader.constants, self.get_constant_by_id, resource_types.RESOURCE_CBUFFER)
            collect_sh_resources(samplers, shader.samplers, self.get_sampler_by_id, resource_types.RESOURCE_SAMPLER)
            collect_sh_resources(textures, shader.textures, self.get_texture_by_id, resource_types.RESOURCE_TEXTURE)
            collect_sh_resources(uavs, shader.uavs, self.get_uav_by_id, resource_types.RESOURCE_UAV)
        for shader in self.pixel_shaders:
            collect_sh_resources(constants, shader.constants, self.get_constant_by_id, resource_types.RESOURCE_CBUFFER)
            collect_sh_resources(samplers, shader.samplers, self.get_sampler_by_id, resource_types.RESOURCE_SAMPLER)
            collect_sh_resources(textures, shader.textures, self.get_texture_by_id, resource_types.RESOURCE_TEXTURE)
            collect_sh_resources(uavs, shader.uavs, self.get_uav_by_id, resource_types.RESOURCE_UAV)
        self.constants = [constants[id] for id in constants]
        self.samplers = [samplers[id] for id in samplers]
        self.textures = [textures[id] for id in textures]
        self.uavs = [uavs[id] for id in uavs]
        mat_params_size = 0
        mat_params_constant = self.get_constant_by_id(0x64D12851)
        if mat_params_constant is not None:
            mat_params_size = max(mat_params_size, mat_params_constant.size << 4)
        for param in self.mat_params:
            mat_params_size = max(mat_params_size, param.offset + param.size)
        self.file_header.mat_params_size = (mat_params_size + 0xF) & ~0xF
        self.update_mat_param_defaults()

    def update_mat_param_defaults(self) -> None:
        self.file_header.has_mat_param_defaults = 1 if self.mat_param_defaults is not None else 0
        if self.mat_param_defaults is not None:
            defaults_len = self.file_header.mat_params_size >> 2
            len_delta = len(self.mat_param_defaults) - defaults_len
            if len_delta > 0:
                self.mat_param_defaults = self.mat_param_defaults[:defaults_len]
            elif len_delta < 0:
                self.mat_param_defaults.extend([0.0 for _ in range(-len_delta)])

    def unsafe_write_header(self, writer: BinaryReader) -> None:
        self.file_header.write(writer)
        for shader in self.vertex_shaders:
            shader.write(writer)
        for shader in self.pixel_shaders:
            shader.write(writer)
        for mat_param in self.mat_params:
            mat_param.write(writer)
        if self.mat_param_defaults is not None:
            writer.write_float(self.mat_param_defaults)
        for constant in self.constants:
            constant.write(writer)
        for sampler in self.samplers:
            sampler.write(writer)
        for texture in self.textures:
            texture.write(writer)
        for uav in self.uavs:
            uav.write(writer)
        for (sk, dv, _) in self.system_keys:
            writer.write_uint32((sk, dv))
        for (sk, dv, _) in self.scene_keys:
            writer.write_uint32((sk, dv))
        for (sk, dv, _) in self.mat_keys:
            writer.write_uint32((sk, dv))
        ((_, dv0, _), (_, dv1, _)) = self.sub_view_keys
        writer.write_uint32((dv0, dv1))
        for node in self.nodes:
            node.write(writer)
        for item in self.items:
            writer.write_uint32(item)
        writer.write_bytes(self.rest)

    def write(self, writer: BinaryReader) -> None:
        self.update()
        self.unsafe_write_header(writer)
        writer.write_bytes(self.blobs)
        writer.write_bytes(self.strings)
