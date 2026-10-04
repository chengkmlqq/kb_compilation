"use client";

/** 菜单管理（独立页，对齐 ds system/menus）。 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Tree,
  TreeSelect,
} from "antd";
import {
  apiCreateMenu,
  apiDeleteMenu,
  apiListMenuIcons,
  apiListMenus,
  apiUpdateMenu,
  SysMenuItem,
} from "@/lib/api";
import { collectSubtreeIds, menusToTreeData } from "../_shared";

export default function SystemMenusPage() {
  const { message } = App.useApp();
  const [items, setItems] = useState<SysMenuItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [menuIcons, setMenuIcons] = useState<string[]>([]);
  const [menuIconsLoading, setMenuIconsLoading] = useState(false);

  // 菜单新建/编辑
  const [menuModalOpen, setMenuModalOpen] = useState(false);
  const [editingMenu, setEditingMenu] = useState<SysMenuItem | null>(null);
  const [menuForm] = Form.useForm();
  const [savingMenu, setSavingMenu] = useState(false);
  const [selectedMenuId, setSelectedMenuId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListMenus();
      if (res.success) setItems(res.data?.items || []);
    } finally {
      setLoading(false);
    }
  }, []);

  // 图标目录（对齐 data-synth icon-actions；菜单 icon 下拉选择用）
  const loadIcons = useCallback(async () => {
    setMenuIconsLoading(true);
    try {
      const res = await apiListMenuIcons();
      if (res.success && res.data) setMenuIcons(res.data);
    } catch {
      /* ignore */
    } finally {
      setMenuIconsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadIcons();
  }, [load, loadIcons]);

  // 编辑时禁用「自身+子孙」作为父级，防成环
  const treeData = editingMenu
    ? menusToTreeData(items, collectSubtreeIds(items, editingMenu.menu_id as string))
    : menusToTreeData(items);

  const openCreate = (parentId?: string) => {
    setEditingMenu(null);
    menuForm.resetFields();
    menuForm.setFieldsValue({ state: "1", sort_num: 1, menu_type: "frame", parent_id: parentId || undefined });
    setMenuModalOpen(true);
  };

  const openEdit = (m: SysMenuItem) => {
    setEditingMenu(m);
    menuForm.setFieldsValue({
      menu_name: m.menu_name,
      menu_label: m.menu_label,
      parent_id: m.parent_id || undefined,
      sort_num: m.sort_num ?? 1,
      menu_icon: m.menu_icon,
      route: m.route,
      state: m.state || "1",
      menu_type: m.menu_type || "frame",
      menu_descr: m.menu_descr,
    });
    setMenuModalOpen(true);
  };

  const doSaveMenu = async () => {
    const values = await menuForm.validateFields();
    setSavingMenu(true);
    try {
      const payload = {
        menu_name: values.menu_name,
        menu_label: values.menu_label,
        parent_id: values.parent_id || null,
        sort_num: values.sort_num,
        menu_icon: values.menu_icon || null,
        route: values.route || null,
        state: values.state,
        menu_type: values.menu_type,
        menu_descr: values.menu_descr,
      };
      const res = editingMenu
        ? await apiUpdateMenu(editingMenu.menu_id as string, payload)
        : await apiCreateMenu(payload);
      if (res.success) {
        message.success(editingMenu ? "菜单已更新" : "菜单已创建");
        setMenuModalOpen(false);
        await load();
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSavingMenu(false);
    }
  };

  const doDeleteMenu = async (menuId: string) => {
    const res = await apiDeleteMenu(menuId);
    if (res.success) {
      message.success("菜单已删除");
      await load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  return (
    <Card
      title="菜单"
      extra={
        <Space>
          <Button disabled={!selectedMenuId} onClick={() => openCreate(selectedMenuId || undefined)}>
            新增子菜单
          </Button>
          <Button type="primary" onClick={() => openCreate()}>
            新增根菜单
          </Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="菜单 route 填写前端页面路径（如 /kbs、/chat），勾选到角色后即成为该角色的可访问页面。已发布（state=1）且被角色引用的菜单才会出现在侧边栏与权限校验中。"
      />
      <Tree
        treeData={treeData}
        defaultExpandAll
        blockNode
        showLine
        selectedKeys={selectedMenuId ? [selectedMenuId] : []}
        onSelect={(keys) => setSelectedMenuId((keys[0] as string) || null)}
        style={{ maxHeight: 380, overflow: "auto" }}
        titleRender={(node) => {
          const m = items.find((x) => x.menu_id === node.key);
          return (
            <Space size={8}>
              <span>{node.title as React.ReactNode}</span>
              {m && (
                <Space size={4}>
                  <Button size="small" type="link" onClick={() => openEdit(m)}>
                    编辑
                  </Button>
                  <Popconfirm title={`删除菜单 ${m.menu_label || m.menu_name}?`} onConfirm={() => void doDeleteMenu(m.menu_id as string)}>
                    <Button size="small" type="link" danger>
                      删除
                    </Button>
                  </Popconfirm>
                  <Button size="small" type="link" onClick={() => openCreate(m.menu_id)}>
                    新增子
                  </Button>
                </Space>
              )}
            </Space>
          );
        }}
      />

      <Modal
        title={editingMenu ? `编辑菜单: ${editingMenu.menu_label || editingMenu.menu_name}` : "新建菜单"}
        open={menuModalOpen}
        onCancel={() => setMenuModalOpen(false)}
        onOk={() => void doSaveMenu()}
        confirmLoading={savingMenu}
        destroyOnClose
      >
        <Form form={menuForm} layout="vertical" initialValues={{ state: "1", menu_type: "frame", sort_num: 1 }}>
          <Form.Item name="menu_name" label="模块编码" rules={[{ required: true, message: "请输入模块编码" }]}>
            <Input placeholder="如 kbs" />
          </Form.Item>
          <Form.Item name="menu_label" label="模块中文名" rules={[{ required: true, message: "请输入模块中文名" }]}>
            <Input placeholder="如 知识库管理" />
          </Form.Item>
          <Form.Item name="route" label="页面路由" extra="前端页面路径，如 /kbs、/chat、/system/users；留空表示仅作分组父节点">
            <Input placeholder="/kbs" />
          </Form.Item>
          <Form.Item name="parent_id" label="父模块">
            <TreeSelect
              treeData={menusToTreeData(items)}
              placeholder="请选择父模块（根节点留空）"
              allowClear
              treeDefaultExpandAll
              fieldNames={{ label: "title", value: "key", children: "children" }}
            />
          </Form.Item>
          <Form.Item name="sort_num" label="排序">
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="menu_icon" label="图标" extra="留空用默认图标">
            <Select
              allowClear
              showSearch
              placeholder="选择图标"
              loading={menuIconsLoading}
              options={menuIcons.map((name) => ({
                label: name,
                value: name,
              }))}
            />
          </Form.Item>
          <Form.Item name="menu_type" label="菜单类型">
            <Radio.Group>
              <Radio value="frame">侧边菜单</Radio>
              <Radio value="nav">顶栏菜单</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item name="state" label="状态">
            <Radio.Group>
              <Radio value="1">发布</Radio>
              <Radio value="0">未发布</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item name="menu_descr" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}