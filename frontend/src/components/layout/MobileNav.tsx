import React from "react";
import { NavLink } from "react-router-dom";
import { Home, FlaskConical, Layers, FileCheck, FileText } from "lucide-react";

export const MobileNav: React.FC = () => {
  const items = [
    { to: "/", label: "Home", icon: <Home className="w-5 h-5" /> },
    { to: "/research", label: "Research", icon: <FlaskConical className="w-5 h-5" /> },
    { to: "/experiments", label: "Experiments", icon: <Layers className="w-5 h-5" /> },
    { to: "/evidence", label: "Evidence", icon: <FileCheck className="w-5 h-5" /> },
    { to: "/reports", label: "Reports", icon: <FileText className="w-5 h-5" /> },
  ];

  return (
    <nav className="md:hidden fixed bottom-0 left-0 right-0 z-40 bg-rex-surface border-t border-rex-border flex items-center justify-around py-2 px-1 select-none backdrop-blur-md">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          className={({ isActive }) =>
            `flex flex-col items-center gap-1 py-1 px-3 rounded-md text-[10px] font-medium transition-colors ${
              isActive
                ? "text-rex-primary font-semibold"
                : "text-rex-muted hover:text-rex-primary"
            }`
          }
        >
          {item.icon}
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );
};
