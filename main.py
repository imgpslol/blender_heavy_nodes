# Nodes that don't move. Add more here if needed. (should stop them all from falling out of frame)
FIXED_TYPES = {'GROUP_INPUT', 'GROUP_OUTPUT', 'OUTPUT_MATERIAL'}


bl_info = {
    "project_name": "blender_heavy_nodes",
    "author": "imgpslol",
    "blender": (5, 2, 2),
}


import bpy
from mathutils import Vector


def find_geo_node_editor(context):
    # Find the first Geometry/Shader Node Editor.
    wm = context.window_manager

    for window in wm.windows:
        for area in window.screen.areas:
            if area.type != 'NODE_EDITOR':
                continue

            for space in area.spaces:
                if (
                    space.type == 'NODE_EDITOR'
                    and (
                        space.tree_type == 'GeometryNodeTree'
                        or space.tree_type == 'ShaderNodeTree'
                    )
                    and space.edit_tree is not None
                ):
                    return window, area, space

    return None, None, None


def link_key(link):
    return (
        link.from_node.name,
        link.from_socket.identifier,
        link.to_node.name,
        link.to_socket.identifier,
    )


class RopeSimSettings(bpy.types.PropertyGroup):
    springiness: bpy.props.FloatProperty(
        name="Springiness",
        default=0.5,
        min=0.0,
        max=1.0,
        description="How bouncy the noodles are",
    )

    damping: bpy.props.FloatProperty(
        name="Damping",
        default=0.98,
        min=0.0,
        max=1.0,
        description="How much energy the noodles keep",
    )

    iterations: bpy.props.IntProperty(
        name="Iterations",
        default=4,
        min=1,
        max=50,
        description="Simulation quality",
    )

    rest_length: bpy.props.FloatProperty(
        name="Length of New Noodles",
        default=200.0,
        min=0.0,
        description="Starting length of new noodles",
    )

    gravity: bpy.props.FloatProperty(
        name="Gravity",
        default=0.6,
        description="Gravity strength",
    )

    enable_tearing: bpy.props.BoolProperty(
        name="Tearing",
        default=False,
        description="Noodles break when stretched too far",
    )

    reset_nodes: bpy.props.BoolProperty(
        name="Reset Nodes",
        default=True,
        description="Reset nodes and restore torn links when stopped",
    )

    tear_threshold: bpy.props.FloatProperty(
        name="Tear Threshold",
        default=2.0,
        min=1.01,
        description="How far a noodle can stretch before breaking",
    )

    update_interval: bpy.props.FloatProperty(
        name="Timer Interval",
        default=0.03,
        min=0.01,
        max=0.5,
        description="Time between simulation steps",
    )

    node_tree_name: bpy.props.StringProperty(
        name="Node Tree",
        description="Node group to simulate. Detects automatically if empty",
    )

    weightednode: bpy.props.StringProperty(
        name="Weighted Node",
        default="XX_HEAVY_XX",
    )

    weightmult: bpy.props.FloatProperty(
        name="Weight Multiplier",
        default=20.0,
        min=0.0,
    )


class NODE_OT_rope_sim_toggle(bpy.types.Operator):
    bl_idname = "node.rope_sim_toggle"
    bl_description = "Start or stop the simulation"
    bl_label = "StartStop"

    _timer = None
    _prev_positions = None
    _node_tree = None
    _area = None
    _rest_lengths = None
    _original_positions = None
    _torn_links = None

    def get_node_tree(self, context):
        settings = context.scene.rope_sim_settings

        if settings.node_tree_name:
            return bpy.data.node_groups.get(settings.node_tree_name)

        window, area, space = find_geo_node_editor(context)

        if space:
            self._area = area
            return space.edit_tree

        return None

    def simstep(self, context):
        tree = self._node_tree
        settings = context.scene.rope_sim_settings

        correction_factor = max(1.0 - settings.springiness, 0.0)
        nodes = list(tree.nodes)
        gravity_vec = Vector((0.0, -settings.gravity))

        # Copy positions before solving.
        positions = {
            node.name: node.location.copy()
            for node in nodes
        }

        fixed = {
            node.name: node.type in FIXED_TYPES
            for node in nodes
        }

        # Moove nodes using their previous position.
        for node in nodes:
            name = node.name

            if fixed[name]:
                self._prev_positions[name] = positions[name]
                continue

            previous = self._prev_positions.get(name, positions[name])
            current = positions[name]

            velocity = (current - previous) * settings.damping
            self._prev_positions[name] = current

            if node.name == settings.weightednode:
                positions[name] = (
                    current
                    + velocity
                    + Vector((0.0, -settings.weightmult))
                )
            else:
                positions[name] = current + velocity + gravity_vec

        # Store the links and their original lenghs.
        links_data = []

        for link in tree.links:
            a_name = link.from_node.name
            b_name = link.to_node.name

            rest_length = self._rest_lengths.get(
                link_key(link),
                settings.rest_length,
            )

            links_data.append({
                'link': link,
                'a': a_name,
                'b': b_name,
                'rest_length': rest_length,
                'from_socket': link.from_socket.identifier,
                'to_socket': link.to_socket.identifier,
            })

        # Keep the connected nodes together.
        for _ in range(settings.iterations):
            for entry in links_data:
                a_name = entry['a']
                b_name = entry['b']
                rest_length = entry['rest_length']

                a_fixed = fixed[a_name]
                b_fixed = fixed[b_name]

                if a_fixed and b_fixed:
                    continue

                a_pos = positions[a_name]
                b_pos = positions[b_name]

                delta = b_pos - a_pos
                distance = delta.length

                if distance < 0.0001:
                    continue

                diff = (distance - rest_length) / distance
                correction = delta * diff * correction_factor

                if a_fixed:
                    positions[b_name] = b_pos - correction

                elif b_fixed:
                    positions[a_name] = a_pos + correction

                else:
                    positions[a_name] = a_pos + correction * 0.5
                    positions[b_name] = b_pos - correction * 0.5

        # Apply the solved positions
        for node in nodes:
            if not fixed[node.name]:
                node.location = positions[node.name]

        # Break links that stretch too far
        if settings.enable_tearing:
            threshold = settings.tear_threshold

            for entry in links_data:
                rest_length = entry['rest_length']

                if rest_length < 0.0001:
                    continue

                delta = positions[entry['b']] - positions[entry['a']]

                if (delta.length / rest_length) > threshold:
                    try:
                        tree.links.remove(entry['link'])
                    except (RuntimeError, ReferenceError):
                        continue

                    self._torn_links.append((
                        entry['a'],
                        entry['from_socket'],
                        entry['b'],
                        entry['to_socket'],
                    ))

        if self._area:
            self._area.tag_redraw()

    def modal(self, context, event):
        if not context.scene.rope_sim_running:
            self.cancel(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            self.simstep(context)

        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        if context.scene.rope_sim_running:
            context.scene.rope_sim_running = False
            return {'CANCELLED'}

        tree = self.get_node_tree(context)

        if tree is None:
            self.report({'ERROR'}, "No node tree found")
            return {'CANCELLED'}

        # Save the starting state
        self._node_tree = tree
        self._prev_positions = {
            node.name: node.location.copy()
            for node in tree.nodes
        }
        self._original_positions = {
            node.name: node.location.copy()
            for node in tree.nodes
        }
        self._torn_links = []

        self._rest_lengths = {
            link_key(link): (
                link.to_node.location - link.from_node.location
            ).length
            for link in tree.links
        }

        context.scene.rope_sim_running = True

        wm = context.window_manager

        self._timer = wm.event_timer_add(
            context.scene.rope_sim_settings.update_interval,
            window=context.window,
        )

        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def cancel(self, context):
        wm = context.window_manager

        if self._timer:
            wm.event_timer_remove(self._timer)
            self._timer = None

        tree = self._node_tree
        settings = context.scene.rope_sim_settings

        if tree is not None and self._original_positions and settings.reset_nodes:

            # Reset node positions.
            for node in tree.nodes:
                original = self._original_positions.get(node.name)

                if original is not None:
                    node.location = original

            # Restore links that were torn.
            for from_name, from_id, to_name, to_id in self._torn_links:
                from_node = tree.nodes.get(from_name)
                to_node = tree.nodes.get(to_name)

                if from_node is None or to_node is None:
                    continue

                from_socket = next(
                    (
                        socket
                        for socket in from_node.outputs
                        if socket.identifier == from_id
                    ),
                    None,
                )

                to_socket = next(
                    (
                        socket
                        for socket in to_node.inputs
                        if socket.identifier == to_id
                    ),
                    None,
                )

                if from_socket and to_socket:
                    tree.links.new(from_socket, to_socket)

            self._torn_links = []

        if self._area:
            self._area.tag_redraw()

        context.scene.rope_sim_running = False


class NODE_PT_rope_sim_panel(bpy.types.Panel):
    bl_label = "Noodle Simulation"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Noodle Sim"

    @classmethod
    def poll(cls, context):
        return (
            context.space_data.tree_type == 'GeometryNodeTree'
            or context.space_data.tree_type == 'ShaderNodeTree'
        )

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.rope_sim_settings

        row = layout.row()
        row.scale_y = 2

        if scene.rope_sim_running:
            row.operator(
                "node.rope_sim_toggle",
                text="Stop",
                icon='PAUSE',
                depress=True,
            )
        else:
            row.operator(
                "node.rope_sim_toggle",
                text="Start",
                icon='PLAY',
            )

        layout.prop(settings, "reset_nodes")

        col = layout.column(align=True)
        col.prop(settings, "springiness", slider=True)
        col.prop(settings, "damping", slider=True)
        col.prop(settings, "gravity")
        col.prop(settings, "rest_length")

        layout.separator()

        col = layout.column(align=True)
        col.prop(settings, "enable_tearing")

        if settings.enable_tearing:
            col.prop(settings, "tear_threshold")

        layout.separator()

        col = layout.column(align=True)
        col.prop(settings, "update_interval")
        col.prop(settings, "iterations")


all_classes = (
    RopeSimSettings,
    NODE_OT_rope_sim_toggle,
    NODE_PT_rope_sim_panel,
)


def register():
    for cls in all_classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.rope_sim_settings = bpy.props.PointerProperty(
        type=RopeSimSettings
    )

    bpy.types.Scene.rope_sim_running = bpy.props.BoolProperty(
        default=False
    )


def unregister():
    del bpy.types.Scene.rope_sim_running
    del bpy.types.Scene.rope_sim_settings

    for cls in reversed(all_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
