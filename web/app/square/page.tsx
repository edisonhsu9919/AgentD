"use client";

import { useEffect, useRef, useState } from "react";
import { useSkillSquareStore } from "@/store/skillSquare";
import { useAuthStore } from "@/store/auth";
import SkillCard from "@/components/square/SkillCard";
import SkillDetailDrawer from "@/components/user/SkillDetailDrawer";
import { showToast } from "@/components/ui/Toast";
import { Search, Package, Upload, Loader2 } from "lucide-react";

export default function SkillSquarePage() {
  const {
    cards,
    cardsLoading,
    searchQuery,
    selectedSkill,
    detail,
    detailLoading,
    actionLoading,
    actionError,
    fetchCards,
    setSearchQuery,
    selectSkill,
    selectSkillVersion,
    installSkill,
    uninstallSkill,
    deleteSkillGlobal,
    importSkill,
  } = useSkillSquareStore();

  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const [showImportModal, setShowImportModal] = useState(false);
  const [importPath, setImportPath] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const detailOpen = drawerOpen && Boolean(selectedSkill);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    fetchCards();
  }, [fetchCards]);

  const openDrawer = (name: string) => {
    setDrawerOpen(true);
    selectSkill(name);
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
  };

  const handleSearch = (value: string) => {
    setSearchQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      fetchCards(value);
    }, 300);
  };

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      {/* Main area: search + grid */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden px-6 py-4">
        <div className="mx-auto flex w-full max-w-6xl items-center gap-3 pb-4">
          {/* Search bar + admin import */}
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-full bg-bg-primary/65 px-4 py-2.5 shadow-[0_12px_32px_rgba(42,41,51,0.04)]">
            <div className="relative min-w-0 flex-1">
              <Search
                size={14}
                className="absolute left-0 top-1/2 -translate-y-1/2 text-text-secondary"
              />
              <input
                type="text"
                placeholder="Search skills..."
                value={searchQuery}
                onChange={(e) => handleSearch(e.target.value)}
                className="w-full bg-transparent py-0.5 pl-6 pr-2 text-sm text-text-primary outline-none placeholder:text-text-secondary/45"
              />
            </div>
          </div>
          {isAdmin && (
            <button
              onClick={() => setShowImportModal(true)}
              className="flex shrink-0 items-center gap-1.5 rounded-full bg-accent px-4 py-2.5 text-xs font-medium text-white shadow-[0_14px_30px_rgba(139,92,246,0.18)] transition hover:bg-accent/90"
            >
              <Upload size={13} />
              Import Skill
            </button>
          )}
        </div>

        {showImportModal && (
          <div className="mx-auto mb-4 w-full max-w-6xl space-y-3 rounded-[22px] bg-bg-primary/55 p-4 shadow-[0_14px_34px_rgba(42,41,51,0.05)]">
            <div className="text-xs font-medium text-text-primary">Import Local Skill</div>
            <input
              type="text"
              value={importPath}
              onChange={(e) => setImportPath(e.target.value)}
              placeholder="Local skill package path (e.g. /skills/my-skill)"
              className="w-full rounded-[14px] bg-white/68 px-3 py-2 text-xs text-text-primary outline-none placeholder:text-text-secondary/45 transition focus:bg-white/86 focus:shadow-[0_0_0_2px_rgba(139,92,246,0.16)]"
            />
            <div className="flex items-center gap-2">
              <button
                onClick={() => { setShowImportModal(false); setImportPath(""); }}
                className="rounded-full px-3 py-1.5 text-xs text-text-secondary transition hover:bg-white/70"
              >
                Cancel
              </button>
              <button
                onClick={async () => {
                  if (!importPath.trim()) return;
                  try {
                    await importSkill(importPath.trim());
                    showToast("info", "Skill imported successfully");
                    setShowImportModal(false);
                    setImportPath("");
                  } catch {
                    showToast("error", "Failed to import skill");
                  }
                }}
                disabled={actionLoading || !importPath.trim()}
                className="flex items-center gap-1.5 rounded-full bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/90 disabled:opacity-50"
              >
                {actionLoading ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
                Import
              </button>
            </div>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-hidden">
          <div className="mx-auto h-full max-w-6xl overflow-y-auto px-1 pb-2">
          {/* Loading */}
          {cardsLoading && cards.length === 0 && (
            <div className="flex items-center justify-center py-12 text-xs text-text-secondary">
              <div className="flex items-center gap-2">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
                Loading skills...
              </div>
            </div>
          )}

          {/* Empty */}
          {!cardsLoading && cards.length === 0 && (
            <div className="flex flex-col items-center gap-3 py-12 text-text-secondary">
              <Package size={32} />
              <span className="text-xs">
                {searchQuery
                  ? "No skills match your search"
                  : "No skills available"}
              </span>
            </div>
          )}

          {/* Grid */}
          {cards.length > 0 && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {cards.map((card) => (
                <SkillCard
                  key={card.name}
                  card={card}
                  selected={selectedSkill === card.name}
                  onSelect={openDrawer}
                />
              ))}
            </div>
          )}
          </div>
        </div>
      </div>

      <div
        className={`fixed inset-0 z-30 bg-[rgba(42,41,51,0.08)] backdrop-blur-[1px] transition-opacity duration-300 ease-out ${
          detailOpen ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={closeDrawer}
      />

      <div
        className={`fixed inset-y-3 right-3 z-40 flex w-[min(46vw,820px)] min-w-[360px] max-w-[820px] transform-gpu flex-col transition-transform duration-[460ms] ease-[cubic-bezier(0.2,0.8,0.2,1)] will-change-transform max-md:inset-x-3 max-md:w-auto max-md:min-w-0 ${
          detailOpen
            ? "translate-x-0"
            : "pointer-events-none translate-x-[calc(100%+1.25rem)]"
        }`}
      >
        {selectedSkill && (
          <SkillDetailDrawer
            detail={detail}
            loading={detailLoading}
            onClose={closeDrawer}
            onVersionChange={selectSkillVersion}
            onInstall={installSkill}
            onUninstall={uninstallSkill}
            onDeleteGlobal={isAdmin ? deleteSkillGlobal : undefined}
            actionLoading={actionLoading}
            actionError={actionError}
          />
        )}
      </div>
    </div>
  );
}
