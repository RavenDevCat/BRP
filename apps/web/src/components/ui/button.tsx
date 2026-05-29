import type { ButtonHTMLAttributes, ReactNode } from "react";
import { buttonClassName, type ButtonVariant } from "@/components/ui/button-styles";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon?: ReactNode;
  variant?: ButtonVariant;
};

export function Button({ className, icon, children, variant = "primary", ...props }: ButtonProps) {
  return (
    <button className={buttonClassName(variant, className)} {...props}>
      {icon}
      {children}
    </button>
  );
}
