'use client';

import React from 'react';
import { createCache, extractStyle, StyleProvider } from '@ant-design/cssinjs';
import type Entity from '@ant-design/cssinjs/es/Cache';
import { useServerInsertedHTML } from 'next/navigation';

import { ConfigProvider, theme as antdTheme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import 'dayjs/locale/zh-cn';
import { modoThemeToken } from '@/theme/antd-theme-token';
import { modoAlgorithm } from '@/theme/modo-algorithm';
import { useThemeMode } from '@/lib/theme-mode';

const StyledComponentsRegistry = ({ children }: React.PropsWithChildren) => {
    const { mode } = useThemeMode();
    const cache = React.useMemo<Entity>(() => createCache(), []);
    useServerInsertedHTML(() => (
        <style id="antd" dangerouslySetInnerHTML={{ __html: extractStyle(cache, true) }} />
    ));
    return (
        <StyleProvider cache={cache}>
            <ConfigProvider
                locale={zhCN}
                theme={{
                    ...modoThemeToken,
                    // 2026-10-12 深色模式对齐 WeKnora：dark 时叠加 darkAlgorithm，
                    // 组件底色/文字/描边自动变暗；modoAlgorithm 品牌色阶保持不随主题。
                    algorithm:
                        mode === 'dark'
                            ? [antdTheme.darkAlgorithm, modoAlgorithm]
                            : [modoAlgorithm],
                }}
            >
                {children}
            </ConfigProvider>
        </StyleProvider>
    );
};

export default StyledComponentsRegistry;