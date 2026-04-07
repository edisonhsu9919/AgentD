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
    clearDetail,
    installSkill,
    uninstallSkill,
    deleteSkillGlobal,
    importSkill,
  } = useSkillSquareStore();

  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const [showImportModal, setShowImportModal] = useState(false);
  const [importPath, setImportPath] = useState("");

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    fetchCards();
  }, [fetchCards]);

  const handleSearch = (value: string) => {
    setSearchQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      fetchCards(value);
    }, 300);
  };

  return (
    <div className="flex h-full">
      {/* Main area: search + grid */}
      <div className="flex flex-1 flex-col overflow-y-auto px-6 py-4">
        <div className="mx-auto w-full max-w-4xl space-y-4">
          {/* Search bar + admin import */}
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search
                size={14}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary"
              />
              <input
                type="text"
                placeholder="Search skills..."
                value={searchQuery}
                onChange={(e) => handleSearch(e.target.value)}
                className="w-full rounded-lg border border-border bg-bg-primary py-2 pl-9 pr-3 text-xs text-text-primary outline-none placeholder:text-text-secondary focus:border-accent"
              />
            </div>
            {isAdmin && (
              <button
                onClick={() => setShowImportModal(true)}
                className="flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-xs font-medium text-white transition hover:bg-accent/90"
              >
                <Upload size={13} />
                Import Skill
              </button>
            )}
          </div>

          {/* Import modal */}
          {showImportModal && (
            <div className="rounded-lg border border-border bg-bg-secondary p-4 space-y-3">
              <div className="text-xs font-medium text-text-primary">Import Local Skill</div>
              <input
                type="text"
                value={importPath}
                onChange={(e) => setImportPath(e.target.value)}
                placeholder="Local skill package path (e.g. /skills/my-skill)"
                className="w-full rounded border border-border bg-bg-primary px-3 py-1.5 text-xs text-text-primary outline-none placeholder:text-text-secondary focus:border-accent"
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={() => { setShowImportModal(false); setImportPath(""); }}
                  className="rounded px-3 py-1.5 text-xs text-text-secondary transition hover:bg-bg-tertiary"
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
                  className="flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/90 disabled:opacity-50"
                >
                  {actionLoading ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
                  Import
                </button>
              </div>
            </div>
          )}

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
                  onSelect={selectSkill}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Detail drawer */}
      {selectedSkill && (
        <div className="w-80 shrink-0 overflow-y-auto">
          <SkillDetailDrawer
            detail={detail}
            loading={detailLoading}
            onClose={clearDetail}
            onVersionChange={selectSkillVersion}
            onInstall={installSkill}
            onUninstall={uninstallSkill}
            onDeleteGlobal={isAdmin ? deleteSkillGlobal : undefined}
            actionLoading={actionLoading}
            actionError={actionError}
          />
        </div>
      )}
    </div>
  );
}
