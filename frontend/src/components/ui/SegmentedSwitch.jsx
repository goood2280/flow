/**
 * 한 칸짜리 트랙 안에 선택지를 나란히 두고, 선택된 쪽으로 흰 버튼(thumb)이 좌우로
 * 미끄러지는 스위치. 선택지는 같은 폭(grid 1fr)이라 thumb 위치는 index 만으로 정해진다.
 *
 * options: [{ value, label, title?, disabled? }]
 */
export function SegmentedSwitch({ value, options = [], onChange, size = "md", className = "", ariaLabel }) {
  const count = Math.max(1, options.length);
  const index = Math.max(0, options.findIndex((option) => option.value === value));
  const hasSelection = options.some((option) => option.value === value);
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={`flow-seg flow-seg--${size}${className ? ` ${className}` : ""}`}
      style={{ "--seg-count": count, "--seg-index": index }}
    >
      {hasSelection && <span className="flow-seg__thumb" aria-hidden="true" />}
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-disabled={option.disabled || undefined}
            title={option.title}
            className={`flow-seg__option${selected ? " is-selected" : ""}${option.disabled ? " is-disabled" : ""}`}
            onClick={() => {
              if (option.disabled || selected) return;
              onChange?.(option.value);
            }}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export default SegmentedSwitch;
