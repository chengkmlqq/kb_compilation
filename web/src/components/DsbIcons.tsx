"use client";

/**
 * DSB（data_supply_base DataSupplyPortalRecordDemo）官方图标复刻（2026-10-16）
 *
 * 来源：modo-design/icon（moda-icon，viewBox 0 0 12 12）与 @arco-design/web-react/icon（viewBox 0 0 48 48）。
 * 为了与 DSB 门户问答页像素级一致，这里直接内联其 SVG 路径（不做自绘），
 * 尺寸统一走 1em（与 .moda-icon / .arco-icon 一致），颜色走 currentColor。
 */

type IconProps = {
  size?: number | string;
  color?: string;
  className?: string;
  style?: React.CSSProperties;
  title?: string;
};

function Svg({ size = 14, color, className, style, title, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 12 12"
      fill="none"
      stroke="none"
      className={`dsb-icon${className ? ` ${className}` : ""}`}
      style={{ fontSize: size, color, ...style }}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  );
}

/** modo IconLayout3Color —— 侧边栏收起/展开（DSB ChatSider 收起钮、Portal 打开侧边栏钮） */
export function DsbIconLayout3(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4.25 1.5v9H10a.5.5 0 0 0 .5-.5V2a.5.5 0 0 0-.5-.5Z" opacity=".25" />
      <path d="M3.5 10.5v-9H2a.5.5 0 0 0-.5.5v8a.5.5 0 0 0 .5.5Z" />
    </Svg>
  );
}

/** modo IconAdd —— 新对话（DSB ChatSider .add 渐变按钮） */
export function DsbIconAdd(props: IconProps) {
  return (
    <Svg {...props}>
      <g fill="none" fillRule="evenodd" stroke="none" strokeWidth="1">
        <path d="M0 0 12 0 12 12 0 12z" />
        <path
          fill="currentColor"
          fillRule="nonzero"
          d="M5.4 10 5.4 6.6 2.05 6.6 2 5.4 5.4 5.4 5.4 2 6.6 2 6.6 5.4 9.95 5.4 10 6.6 6.6 6.6 6.6 10z"
        />
      </g>
    </Svg>
  );
}

/** modo IconOperateColor —— 会话项「操作」入口（DSB .item-oper） */
export function DsbIconOperate(props: IconProps) {
  return (
    <Svg {...props}>
      <path
        d="M3.634 5.5H1.5v1h2.134a1.001 1.001 0 0 1 0-1Zm1.732 1a1.001 1.001 0 0 0 0-1H10.5v1ZM3.5 6a1 1 0 1 1 1 1 1 1 0 0 1-1-1Z"
        opacity=".55"
      />
      <path d="M6.634 2H1.5v1h5.134a1.001 1.001 0 0 1 0-1Zm1.732 1a1.001 1.001 0 0 0 0-1H10.5v1ZM6.5 2.5a1 1 0 1 1 1 1 1 1 0 0 1-1-1Z" />
      <path
        d="M6.634 9H1.5v1h5.134a1.001 1.001 0 0 1 0-1Zm1.732 1a1.001 1.001 0 0 0 0-1H10.5v1ZM7.5 8.5a1 1 0 1 0 1 1 1 1 0 0 0-1-1Z"
        opacity=".25"
      />
    </Svg>
  );
}

/** modo IconSwapRight —— 推荐问题右侧箭头（DSB .rela-item .moda-icon） */
export function DsbIconSwapRight(props: IconProps) {
  return (
    <Svg {...props}>
      <g fill="none" fillRule="evenodd" stroke="none" strokeWidth="1">
        <path d="M0 0 12 0 12 12 0 12z" />
        <path
          fill="currentColor"
          fillRule="nonzero"
          d="M8.3121547 5.4 5.32872928 2.3 6.12430939 1.5 10.5 6 6.12430939 10.5 5.32872928 9.7 8.3121547 6.6 1.5 6.6 1.5 5.45 8.3121547 5.45z"
        />
      </g>
    </Svg>
  );
}

/** modo IconCopy —— 复制回答（DSB .assistant-footer） */
export function DsbIconCopy(props: IconProps) {
  return (
    <Svg {...props}>
      <g fill="none" fillRule="evenodd" stroke="none" strokeWidth="1">
        <path d="M0 0 12 0 12 12 0 12z" />
        <path
          fill="currentColor"
          fillRule="nonzero"
          d="M3.65 3V1.5c0-.3.2-.5.5-.5h6.1c.25 0 .5.2.5.5v7c0 .3-.2.5-.5.5h-1.9v1.5c0 .3-.2.5-.5.5h-6.1c-.25 0-.5-.2-.5-.5v-7c0-.3.2-.5.5-.5h1.9Zm-1.4 1v6h5.1V4h-5.1Zm2.4-1h3.7v5h1.4V2h-5.1v1ZM3.4 5.5h3v1h-3v-1Zm0 2h3v1h-3v-1Z"
        />
      </g>
    </Svg>
  );
}

/** modo IconRefresh —— 重新生成（DSB .assistant-footer） */
export function DsbIconRefresh(props: IconProps) {
  return (
    <Svg {...props}>
      <g fill="none" fillRule="evenodd" stroke="none" strokeWidth="1">
        <path d="M0 0 12 0 12 12 0 12z" />
        <path
          fill="currentColor"
          fillRule="nonzero"
          d="M2.75 2.2C3.65 1.4 4.8 1 6 1c2.75 0 5 2.25 5 5 0 1.05-.35 2.05-.9 2.85L8.5 6H10c0-2.2-1.8-4-4-4-1.05 0-2.05.4-2.75 1.1l-.5-.9Zm6.5 7.6C8.35 10.6 7.2 11 6 11c-2.75 0-5-2.25-5-5 0-1.05.35-2.05.9-2.85L3.5 6H2c0 2.2 1.8 4 4 4 1.05 0 2.05-.4 2.75-1.1l.5.9Z"
        />
      </g>
    </Svg>
  );
}

/** modo IconAgree —— 喜欢（激活色 DSB .agree-icon = warning-6） */
export function DsbIconAgree(props: IconProps) {
  return (
    <Svg {...props}>
      <g fill="none" fillRule="evenodd" stroke="none" strokeWidth="1">
        <path d="M0 0 12 0 12 12 0 12z" />
        <path
          fill="currentColor"
          fillRule="nonzero"
          d="M10.45 6.226c.2-.251.3-.603.3-.905 0-.502-.3-1.055-.85-1.306-.1-.05-.2-.05-.35-.05H6.8l.05-1.358c0-.352-.1-.704-.35-.955a1.482 1.482 0 0 0-1-.402c-.65 0-1.2.402-1.35 1.005l-1 3.519h-1.5c-.2 0-.4.15-.4.402v4.172c0 .2.2.402.4.402H8.6c.25 0 .6-.05.8-.251.35-.252.65-.754.65-1.207 0-.15 0-.301-.05-.352.2-.25.3-.603.3-.904 0-.151 0-.302-.05-.352.2-.251.3-.603.3-.905-.05-.301-.05-.452-.1-.553ZM3 9.846h-.85V6.627H3v3.217Zm6.6-4.122-.25.25.15.302c.05.101.05.202.05.252 0 .15-.05.301-.2.452l-.25.251.15.302c.05.1.05.201.05.251 0 .151-.05.302-.2.453l-.25.301.15.302c.3.503-.05 1.056-.3 1.005H3.85V6.427L5 2.255c.05-.2.35-.402.6-.352.35.05.35.453.35.553l-.1 2.313H9.4c.25.1.5.603.2.955Z"
        />
      </g>
    </Svg>
  );
}

/** modo IconDisagree —— 不喜欢（激活色 DSB .disagree-icon = danger-6） */
export function DsbIconDisagree(props: IconProps) {
  return (
    <Svg {...props}>
      <g fill="none" fillRule="evenodd" stroke="none" strokeWidth="1">
        <path d="M0 0 12 0 12 12 0 12z" />
        <path
          fill="currentColor"
          fillRule="nonzero"
          d="M10.473 5.606c.053-.105.053-.262.053-.367 0-.315-.106-.63-.316-.892.052-.105.052-.263.052-.368 0-.315-.105-.63-.316-.892.053-.105.053-.262.053-.367 0-.42-.158-.788-.422-1.05-.263-.263-.685-.42-1.054-.42H1.672a.415.415 0 0 0-.422.42v4.146c0 .21.21.42.422.42h1.475L4.15 9.753c.158.577.738.997 1.37.997.369 0 .685-.157.949-.367.263-.263.369-.578.369-.945l-.053-1.365h2.74c.106 0 .264 0 .37-.052.526-.263.843-.788.843-1.312.052-.525-.053-.84-.264-1.103Zm-7.378-.42H2.25V2.037h.844v3.15Zm6.43 1.837H5.781l.106 2.31c0 .105-.053.21-.158.315-.053.052-.158.052-.211.052-.21 0-.422-.157-.474-.315l-1.16-4.094V2.09h4.849c.21.105.369.315.369.525 0 .105 0 .21-.053.262l-.158.315.263.262c.159.105.211.263.211.473 0 .105 0 .21-.052.262l-.159.315.264.263c.158.105.21.262.21.472 0 .105 0 .21-.052.262l-.158.315.264.263c.158.105.21.262.21.472-.052.158-.158.368-.369.472Z"
        />
      </g>
    </Svg>
  );
}

/** modo IconDataGovernanceColor —— 反思模式按钮图标（DSB .reflect-mode-switch-btn） */
export function DsbIconDataGovernance(props: IconProps) {
  return (
    <Svg {...props}>
      <path
        d="M6.239 1.115a3.411 3.411 0 0 0-.209-.007 3.268 3.268 0 0 0-2.657 5.148l.043.063a.893.893 0 0 0-.03.209.976.976 0 1 0 .977-.983.924.924 0 0 0-.18.037 2.186 2.186 0 0 1-.37-1.34A2.234 2.234 0 0 1 6.03 2.145l.142.004a2.274 2.274 0 0 1 2.087 1.99 3.355 3.355 0 0 1 1.012.244A3.266 3.266 0 0 0 6.24 1.115Z"
        opacity=".55"
      />
      <path
        d="M7.892 5.396a2.184 2.184 0 0 1 1.228.38 2.254 2.254 0 0 1 .594 3.111 2.2 2.2 0 0 1-1.706.971l-.137.004a2.644 2.644 0 0 1-.921-.18 3.438 3.438 0 0 1-.724.753 3.26 3.26 0 0 0 1.65.457c.068 0 .203-.006.203-.006a3.203 3.203 0 0 0 2.486-1.417 3.291 3.291 0 0 0-.867-4.549 3.204 3.204 0 0 0-1.807-.563c-.058 0-.114.002-.171.005a1.244 1.244 0 0 0-.363.079.948.948 0 0 0-.568-.165.982.982 0 1 0 1.023 1.13Z"
        opacity=".25"
      />
      <path d="m7.033 9.061.013-.002a.982.982 0 1 0-1.175-.935.958.958 0 0 0 .18.51 2.166 2.166 0 0 1-.937.956 2.27 2.27 0 0 1-.927.232 2.237 2.237 0 0 1-2.056-1.243A2.294 2.294 0 0 1 2.8 5.766a3.454 3.454 0 0 1-.297-1.006 3.269 3.269 0 0 0 1.713 6.098l.098-.004a3.215 3.215 0 0 0 1.258-.335A3.234 3.234 0 0 0 7.033 9.06Z" />
    </Svg>
  );
}

/** modo IconModelMgrColor —— 模型选择按钮图标（DSB .portal-chat-dropdown-btn） */
export function DsbIconModelMgr(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M10.625 4.034a1.187 1.187 0 0 0-.161-.596L6 6v5.125a1.205 1.205 0 0 0 .6-.16L10.024 9a1.192 1.192 0 0 0 .6-1.033Z" opacity=".25" />
      <path d="m6 6 4.464-2.562a1.197 1.197 0 0 0-.44-.437L6.6 1.035a1.206 1.206 0 0 0-1.2 0L1.975 3a1.195 1.195 0 0 0-.44.437Z" opacity=".55" />
      <path d="M1.536 3.438a1.187 1.187 0 0 0-.161.596v3.932a1.192 1.192 0 0 0 .6 1.033l2.382 1.368 1.043.598a1.205 1.205 0 0 0 .6.16V6Z" />
    </Svg>
  );
}

/** modo IconLinkColor —— 附件（DSB Sender .file-text-btn） */
export function DsbIconLink(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8.147 1.142a.487.487 0 0 0-.688 0L4.907 3.694a.363.363 0 0 0 0 .514l1.042 1.04 1.073-1.073a.584.584 0 0 1 .78-.055.567.567 0 0 1 .04.84L6.75 6.055l1.128 1.124a.243.243 0 0 0 .344 0l2.636-2.636a.487.487 0 0 0 0-.688Z" />
      <path
        d="M6.052 6.753 4.978 7.825a.583.583 0 0 1-.779.054.568.568 0 0 1-.079-.8l.037-.04L5.25 5.947 4.122 4.823a.243.243 0 0 0-.344 0L1.143 7.459a.487.487 0 0 0 0 .688l2.71 2.71a.487.487 0 0 0 .688 0l2.636-2.635a.243.243 0 0 0 0-.344Z"
        opacity=".25"
      />
    </Svg>
  );
}

function Chevron({ up, ...props }: IconProps & { up?: boolean }) {
  const { size = 14, color, className, style, title } = props;
  return (
    <svg
      viewBox="0 0 48 48"
      fill="none"
      stroke="currentColor"
      strokeWidth="4"
      className={`dsb-icon${className ? ` ${className}` : ""}`}
      style={{ fontSize: size, color, ...style }}
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      <path d={up ? "M39.6 30.557 24.043 15 8.487 30.557" : "M39.6 17.443 24.043 33 8.487 17.443"} />
    </svg>
  );
}

/** arco IconUp / IconDown —— 思考区、引用列表折叠箭头 */
export function DsbIconChevronUp(props: IconProps) {
  return <Chevron up {...props} />;
}
export function DsbIconChevronDown(props: IconProps) {
  return <Chevron {...props} />;
}

/** @ant-design/x StopLoading —— 停止生成（DSB Sender LoadingButton 图标） */
export function DsbStopLoading(props: IconProps) {
  const { size = 32, color, className, style, title } = props;
  return (
    <svg
      viewBox="0 0 1000 1000"
      xmlns="http://www.w3.org/2000/svg"
      color={color}
      className={`dsb-icon dsb-stop-loading${className ? ` ${className}` : ""}`}
      style={{ fontSize: size, ...style }}
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      <rect fill="currentColor" height="250" rx="24" ry="24" width="250" x="375" y="375" />
      <circle cx="500" cy="500" fill="none" r="450" stroke="currentColor" strokeWidth="100" opacity="0.45" />
      <circle cx="500" cy="500" fill="none" r="450" stroke="currentColor" strokeWidth="100" strokeDasharray="600 9999999">
        <animateTransform attributeName="transform" dur="1s" from="0 500 500" repeatCount="indefinite" to="360 500 500" type="rotate" />
      </circle>
    </svg>
  );
}

/** modo IconDataCatalogMgrColor —— 知识集/知识库（DSB 知识集选择面板卡片图标，Color 变体分层 opacity） */
export function DsbIconDataCatalog(props: IconProps) {
  return (
    <Svg {...props}>
      <path
        fill="currentColor"
        d="M3.306 1.515h-2.26a.245.245 0 0 0-.245.245v8.498a.245.245 0 0 0 .245.245h2.26a.245.245 0 0 0 .245-.245V1.76a.245.245 0 0 0-.245-.245ZM2.8 7.39H1.55v-.75H2.8Zm0-1.5H1.55v-.75H2.8Zm0-1.5H1.55v-.75H2.8Zm3.356 1.125h-1.25v-.75h1.25Zm0 .75h-1.25v.75h1.25Z"
      />
      <path
        fill="currentColor"
        opacity=".25"
        d="M6.532 1.497h-2a.25.25 0 0 0-.25.25v8.5a.25.25 0 0 0 .25.25h2a.25.25 0 0 0 .25-.25v-8.5a.25.25 0 0 0-.25-.25Zm-.375 5.518h-1.25v-.75h1.25Zm0-1.5h-1.25v-.75h1.25Zm5.018 4.23a.252.252 0 0 1-.205.293l-1.772.312a.252.252 0 0 1-.292-.204L7.518 2.272a.252.252 0 0 1 .204-.292l1.772-.313a.252.252 0 0 1 .293.205Z"
      />
    </Svg>
  );
}
