"use client";

/**
 * DSB arco Pagination(simple) 复刻：`共 N 条 ‹ [页码输入] / 总页数 › 30 条/页`
 *
 * 从 kbs/page.tsx 抽出（原为知识库管理页内联实现），供「知识库管理页」与
 * 「知识库详情 · 文档列表」共用同一分页形态，避免详情页回落成 antd 常驻分页。
 *
 * 行为（与 DSB 一致）：仅当总数超过一页时才渲染分页条。
 */
import { useEffect, useState } from "react";
import { Input, Select } from "antd";
import { ChevronLeftIcon, ChevronRightIcon } from "./KbsIcons";

export interface KbsSimplePaginationProps {
  current: number;
  pageSize: number;
  total: number;
  /** 翻页 / 改每页条数；改每页条数时 current 归 1（与知识库管理页一致） */
  onChange: (page: number, pageSize: number) => void;
  /** 左侧总数文案（DSB 为「共 N 条」） */
  showTotal?: (total: number) => string;
  pageSizeOptions?: number[];
  /** 外层容器类名（默认 .kb-kbs-pagination-bar 贴底；文档列表传 .kb-doc-pagination-bar 走文档列布局） */
  containerClassName?: string;
}

export default function KbsSimplePagination({
  current,
  pageSize,
  total,
  onChange,
  showTotal = (t) => `共 ${t} 条`,
  pageSizeOptions = [10, 20, 30, 40, 50],
  containerClassName = "kb-kbs-pagination-bar",
}: KbsSimplePaginationProps) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const [jumpText, setJumpText] = useState(String(current));

  useEffect(() => {
    setJumpText(String(current));
  }, [current]);

  if (total <= pageSize) return null;

  const commitJump = () => {
    const n = Number.parseInt(jumpText, 10);
    const next = Number.isNaN(n) ? current : Math.min(Math.max(n, 1), pageCount);
    setJumpText(String(next));
    if (next !== current) onChange(next, pageSize);
  };

  return (
    <div className={containerClassName}>
      <div className="kb-kbs-pagination">
        <div className="kb-kbs-pagination-total">{showTotal(total)}</div>
        <ul className="kb-kbs-pagination-list">
          <li
            className={`kb-kbs-pagination-item kb-kbs-pagination-item-prev${
              current <= 1 ? " kb-kbs-pagination-item-disabled" : ""
            }`}
            onClick={() => {
              if (current > 1) onChange(current - 1, pageSize);
            }}
          >
            <ChevronLeftIcon />
          </li>
          <li className="kb-kbs-pagination-item-simple-pager">
            <Input
              className="kb-kbs-pagination-jumper-input"
              value={jumpText}
              onChange={(e) => setJumpText(e.target.value)}
              onBlur={commitJump}
              onPressEnter={commitJump}
              disabled={pageCount <= 1}
            />
            <span className="kb-kbs-pagination-separator">/</span>
            <span>{pageCount}</span>
          </li>
          <li
            className={`kb-kbs-pagination-item kb-kbs-pagination-item-next${
              current >= pageCount ? " kb-kbs-pagination-item-disabled" : ""
            }`}
            onClick={() => {
              if (current < pageCount) onChange(current + 1, pageSize);
            }}
          >
            <ChevronRightIcon />
          </li>
        </ul>
        <div className="kb-kbs-pagination-option">
          <Select
            value={pageSize}
            style={{ width: 96 }}
            options={pageSizeOptions.map((n) => ({ label: `${n} 条/页`, value: n }))}
            onChange={(v) => onChange(1, v)}
          />
        </div>
      </div>
    </div>
  );
}
