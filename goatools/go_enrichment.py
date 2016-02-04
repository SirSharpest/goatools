#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
python %prog study.file population.file gene-association.file

This program returns P-values for functional enrichment in a cluster of
study genes using Fisher's exact test, and corrected for multiple testing
(including Bonferroni, Holm, Sidak, and false discovery rate)
"""

from __future__ import absolute_import

import sys
import fisher

from .multiple_testing import Bonferroni, Sidak, HolmBonferroni, FDR, calc_qval
from .ratio import count_terms, is_ratio_different


class GOEnrichmentRecord(object):
    """Represents one result (from a single GOTerm) in the GOEnrichmentStudy
    """
    # Fields seen in every enrichment result
    _fields = ["id", "enrichment", "description", "ratio_in_study", "ratio_in_pop", "p_uncorrected"]
    _fldfmt = ["%s"] * 3 + ["%d/%d"] * 2 + ["%.3g"]

    def __init__(self, **kwargs):
        # Methods seen in current enrichment result
        self._methods = [] 
        for k, v in kwargs.items():
            setattr(self, k, v)

        self.goterm = None  # the reference to the GOTerm

    def set_corrected_pval(self, method, pvalue):
        self._methods.append(method)
        setattr(self, "".join(["p_", method]), pvalue)

    def __str__(self, indent=False):
        field_data = [getattr(self, f, "n.a.") for f in self._fields] + \
                     [getattr(self, "p_{}".format(m)) for m in self._methods]
        field_formatter = [f for f in self._fldfmt] + ["%.3g"]*len(self._methods)
        assert len(field_data) == len(field_formatter)

        # default formatting only works for non-"n.a" data
        for i, f in enumerate(field_data):
            if f == "n.a.":
                field_formatter[i] = "%s"

        # print dots to show the level of the term
        dots = ""
        if self.goterm is not None and indent:
            dots = "." * self.goterm.level

        return dots + "\t".join(a % b for (a, b) in
                                zip(field_formatter, field_data))

    def __repr__(self):
        return "GOEnrichmentRecord({GO})".format(GO=self.id)

    def set_goterm(self, go):
        self.goterm = go.get(self.id, None)
        self.description = self.goterm.name if self.goterm is not None else "n.a."

    def update_remaining_fields(self, min_ratio=None):
        study_count, study_n = self.ratio_in_study
        pop_count, pop_n = self.ratio_in_pop
        self.enrichment = 'e' if ((1.0 * study_count / study_n) >
                                  (1.0 * pop_count / pop_n)) else 'p'

        self.is_ratio_different = is_ratio_different(min_ratio, study_count,
                                                     study_n, pop_count, pop_n)


class GOEnrichmentStudy(object):
    """Runs Fisher's exact test, as well as multiple corrections
    """
    all_methods = ("bonferroni", "sidak", "holm", "fdr")

    def __init__(self, pop, assoc, obo_dag, propagate_counts=True,
                 alpha=.05,
                 methods=["bonferroni", "sidak", "holm"]):

        self.pop = pop
        self.pop_n = len(pop)
        self.assoc = assoc
        self.obo_dag = obo_dag
        self.alpha = alpha
        self.methods = methods

        if propagate_counts:
            print >> sys.stderr, "Propagating term counts to parents .."
            obo_dag.update_association(assoc)
        self.term_pop = count_terms(pop, assoc, obo_dag)


    def run_study(self, study, **kws):
        """Run Gene Ontology Enrichment Study (GOEA) on study ids."""
        # Calculate uncorrected pvalues
        results = self._get_pval_uncorr(study)

        # Do multipletest corrections on uncorrected pvalues and update results
        methods = kws['methods'] if 'methods' in kws else self.methods
        alpha = kws['alpha'] if 'alpha' in kws else self.alpha
        self._run_multitest_corr(results, methods, alpha, study)

        results.sort(key=lambda r: r.p_uncorrected)

        for rec in results:
            # get go term for description and level
            rec.set_goterm(self.obo_dag)

        return results

    def _get_pval_uncorr(self, study):
        """Calculate the uncorrected pvalues for study items."""
        results = []
        term_study = count_terms(study, self.assoc, self.obo_dag)
        pop_n, study_n = self.pop_n, len(study)
        allterms = set(term_study.keys() + self.term_pop.keys())

        for term in allterms:
            study_count = term_study.get(term, 0)
            pop_count = self.term_pop.get(term, 0)
            p = fisher.pvalue_population(study_count, study_n,
                                         pop_count, pop_n)

            one_record = GOEnrichmentRecord(
                id=term,
                p_uncorrected=p.two_tail,
                ratio_in_study=(study_count, study_n),
                ratio_in_pop=(pop_count, pop_n))

            results.append(one_record)
          
        return results
        
    def _run_multitest_corr(self, results, methods, alpha, study):
        """Do multiple-test corrections on uncorrected pvalues."""
        pvals = [r.p_uncorrected for r in results]
        bonferroni, sidak, holm, fdr = None, None, None, None

        for method in methods:
            if method == "bonferroni":
                bonferroni = Bonferroni(pvals, alpha).corrected_pvals
            elif method == "sidak":
                sidak = Sidak(pvals, alpha).corrected_pvals
            elif method == "holm":
                holm = HolmBonferroni(pvals, alpha).corrected_pvals
            elif method == "fdr":
                # get the empirical p-value distributions for FDR
                p_val_distribution = calc_qval(len(study),
                                               self.pop_n,
                                               self.pop, self.assoc,
                                               self.term_pop, self.obo_dag)
                fdr = FDR(p_val_distribution,
                          results, alpha).corrected_pvals
            else:
                raise Exception("INVALID METHOD({MX}). VALID METHODS: {Ms}".format(
                                MX=method, Ms=" ".join(self.all_methods)))

        all_corrections = (bonferroni, sidak, holm, fdr)

        for method, corrected_pvals in zip(self.all_methods, all_corrections):
            self._update_results(results, method, corrected_pvals)

    @staticmethod
    def _update_results(results, method, corrected_pvals):
        """Add data members to store multiple test corrections."""
        if corrected_pvals is None:
            return
        for rec, val in zip(results, corrected_pvals):
            rec.set_corrected_pval(method, val)

    @staticmethod
    def print_summary(results, min_ratio=None, indent=False, pval=0.05):
        from .version import __version__ as version
        from datetime import date

        # Header contains provenance and parameters
        print("# Generated by GOATOOLS v{0} ({1})".format(version, date.today()))
        print("# min_ratio={0} pval={1}".format(min_ratio, pval))

        # field names for output
        print("\t".join(GOEnrichmentRecord._fields))

        for rec in results:
            # calculate some additional statistics
            # (over_under, is_ratio_different)
            rec.update_remaining_fields(min_ratio=min_ratio)

            if pval is not None and rec.p_uncorrected >= pval:
                continue

            if rec.is_ratio_different:
                print(rec.__str__(indent=indent))
