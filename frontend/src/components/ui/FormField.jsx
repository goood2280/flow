import { cloneElement, isValidElement, useId } from "react";

export function FormField({
  id,
  label,
  help,
  error,
  required = false,
  children,
  className = "",
}) {
  const generatedId = useId();
  const controlId = id || `flow-field-${generatedId.replace(/:/g, "")}`;
  const helpId = help && !error ? `${controlId}-help` : undefined;
  const errorId = error ? `${controlId}-error` : undefined;
  const describedBy = [helpId, errorId].filter(Boolean).join(" ") || undefined;
  const control = isValidElement(children)
    ? cloneElement(children, {
        id: children.props.id || controlId,
        required: children.props.required ?? required,
        "aria-describedby": children.props["aria-describedby"] || describedBy,
        "aria-invalid": children.props["aria-invalid"] ?? (error ? true : undefined),
      })
    : children;

  return (
    <div className={`ds-form-field${className ? ` ${className}` : ""}`}>
      <label className="ds-form-field__label" htmlFor={controlId}>
        {label}
        {required && <span className="ds-form-field__required" aria-hidden="true"> *</span>}
      </label>
      {control}
      {helpId && <div id={helpId} className="ds-form-field__help">{help}</div>}
      {errorId && <div id={errorId} className="ds-form-field__error">{error}</div>}
    </div>
  );
}

export default FormField;
