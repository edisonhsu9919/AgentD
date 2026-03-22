"use client";

import { useEffect, useRef } from "react";
import { useSkillSquareStore } from "@/store/skillSquare";
import SkillCard from "@/components/square/SkillCard";
import SkillDetailDrawer from "@/components/user/SkillDetailDrawer";
import { Search, Package } from "lucide-react";

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
  } = useSkillSquareStore();

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
          {/* Search bar */}
          <div className="relative">
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
            actionLoading={actionLoading}
            actionError={actionError}
          />
        </div>
      )}
    </div>
  );
}
