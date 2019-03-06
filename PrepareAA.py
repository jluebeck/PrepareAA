#!/usr/bin/env python

import sys
import os
import threading
from subprocess import call
import argparse
import gzip

#generic worker thread function
class workerThread(threading.Thread):
	def __init__(self, threadID, target, *args):
		threading.Thread.__init__(self)
		self.threadID = threadID
		self._target = target
		self._args = args
		threading.Thread.__init__(self)

	def run(self): 
		self._target(*self._args)

#THIS IS DESIGNED TO WORK FOR ANALYSIS WITH HG19.
def run_bwa(ref,fastqs,outdir,sname,nthreads):
	outname = outdir + sname
	print(outname)
	print("Performing alignment and sorting")	
 	cmd = "{{ bwa mem -t {} {} {} | samtools view -Shu - | samtools sort -@4 - {}.cs; }} 2>{}_aln_stage.stderr".format(nthreads, ref, fastqs,outname,outname)
 	print(cmd)
 	call(cmd,shell=True)
 	print("Performing duplicate removal & indexing")
 	cmd = "samtools rmdup -s {}.cs.bam {}.cs.rmdup.bam &> rmdup.out".format(outname,outname)
 	print(cmd)
	call(cmd,shell=True)
	print("Running samtools index")
	cmd = "samtools index {}.cs.rmdup.bam".format(outname)
	print(cmd)
	call(cmd,shell=True)
	print("Removing temp BAM")
	cmd = "rm {}.cs.bam".format(outname)
	call(cmd,shell=True)
	return outname + ".cs.rmdup.bam"

def run_freebayes(ref,bam_file,outdir,sname,nthreads,regions):
	#-f is fasta
	#-r is region to call
	while True:
		try:
			curr_region_tup = regions.pop()
		except IndexError:
			break
		
		curr_region_string = curr_region_tup[0] + ":" + curr_region_tup[1]
		print(curr_region_string)

		vcf_file = outdir + sname + "_" + curr_region_tup[0] + "_" + curr_region_tup[2] + ".vcf"
		'''
		SWAP OUT THE FILTER FIELD, ONLY ON NON HEADER LINES (unless you can fix it) with another if
		awk '{ $7 = ($7 == "." ? "PASS" : $7) } 1' OFS="\t"
		'''

		replace_filter_field_func = "awk '{ if (substr($1,1,1) != \"#\" ) { $7 = ($7 == \".\" ? \"PASS\" : $7 ) }} 1 ' OFS=\"\\t\""
		cmd = "freebayes --genotype-qualities --standard-filters --use-best-n-alleles 5 --max-coverage 25000 --strict-vcf -f {} -r {} {} | {} > {}".format(ref, curr_region_string, bam_file, replace_filter_field_func, vcf_file)	
		#print(cmd)
		call(cmd,shell=True)
		#gzip the new VCF
		print("gzipping VCF")
		call("gzip -f " + vcf_file,shell=True)

def run_canvas(bam_file, vcf_file, outdir, canvas_data_dir, removed_regions_bed, sname, ref):
	print("Calling Canvas")
	# -b: bam
	# --sample-b-allele-vcf: vcf
	# -n: sample name
	# -o: output directory
	#-r: reference fasta
	#-g: "folder with genome.fa and genomesize xml
	#-f: regions to ignore

	cmd = "Canvas Germline-WGS -b {} --sample-b-allele-vcf={} --ploidy-vcf={} -n {} -o {} -r {} -g {} -f {} > {}/canvas_stdout.log".format(bam_file, vcf_file, ploidy_vcf, sname, outdir, ref, canvas_data_dir, removed_regions_bed, outdir)
	print(cmd)
	call(cmd,shell=True,executable="/bin/bash")

def merge_and_filter_vcfs(chr_names,vcf_list,outdir,sname):
	#sort the elements in hg19 chr sizes
	print("Merging VCFs and zipping")
	merged_vcf_file = outdir + sname + "_merged.vcf"
	relevant_vcfs = [x for x in vcf_list if any([i in x for i in chr_names])]
	chrom_vcf_d = {}
	for f in relevant_vcfs:
		curr_chrom = f.rsplit(".vcf.gz")[0].rsplit("_")[-2:]
		chrom_vcf_d[curr_chrom[0] + curr_chrom[1]] = f

	chr_nums = [x[3:] for x in chr_names]
	numeric_chr_names = []
	for x in chr_nums:
		try:
			numeric_chr_names.append(int(x))
		except ValueError:
			numeric_chr_names.append(x)

	sorted_chr_names = ["chr" + str(x) for x in sorted(numeric_chr_names)]
	#include the header from the first one
	call("zcat " + chrom_vcf_d["chrM"] + " | awk '$4 != \"N\"' > " + merged_vcf_file,shell=True)

	#zcat the rest, grepping out all header lines starting with "#"
	for i in sorted_chr_names:
		if i == "chrM":
			continue
		call("zcat " + chrom_vcf_d[i + "p"] + " | grep -v \"^#\" | awk '$4 != \"N\"' >> " + merged_vcf_file,shell=True)
		call("zcat " + chrom_vcf_d[i + "q"] + " | grep -v \"^#\" | awk '$4 != \"N\"' >> " + merged_vcf_file,shell=True)


	call("gzip -f " + merged_vcf_file,shell=True)

	return merged_vcf_file + ".gz"

def convert_canvas_cnv_to_seeds(canvas_output_directory,sorted_bam,output_directory,sname):
	
	#enter the canvas folder
	#open CNV.vcf.gz
	#convert to bed

	#call amplified_intervals.py
	with gzip.open(canvas_output_directory + "/CNV.vcf.gz", 'rb') as infile, open(canvas_output_directory + "/CNV_GAIN.bed",'w') as outfile:
		for line in infile:
			if line.startswith("#"):
				if line.startswith("#CHROM"):
					head_fields = line[1:].rstrip().rsplit("\t")

			else:
				fields = line.rstrip().rsplit("\t")
				line_dict = dict(zip(head_fields,fields))
				if "GAIN" in fields[2]:
					chrom = fields[0]
					start = fields[1]
					end = fields[2].rsplit(":")[3].rsplit("-")[1]
					chrom_num = fields[-1].rsplit(":")[3]
					outfile.write(chrom + "\t" + start + "\t" + end + "\t" + fields[4] + "\t" + chrom_num + "\n")

	#call amplified_intervals.py from $AA_SRC
	CNV_seeds_filename = "{}/{}_AA_CNV_SEEDS.bed".format(output_directory, sname)
	cmd = "python {}/amplified_intervals.py --bed {} --bam {} | grep '^chr\\|^[1-2]\\|^X\\|^Y' > {}".format(AA_SRC, canvas_output_directory + "/CNV_GAIN.bed",sorted_bam,CNV_seeds_filename)
	call(cmd,shell=True)
	return CNV_seeds_filename


def run_AA(amplified_interval_bed, sorted_bam, AA_outdir, sname):
	print("Running AA with default arguments. To change settings run AA separately.")
	cmd = "{}/AmpliconArchitect.py --bed {} --bam {} --out {}/{}".format(AA_SRC,amplified_interval_bed,sorted_bam,AA_outdir,sname)
	call(cmd,shell=True)

def get_ref_sizes(ref_genome_size_file):
	chr_sizes = {}
	with open(ref_genome_size_file) as infile:
		for line in infile:
			fields = line.rstrip().rsplit()
			chr_sizes[fields[0]] = str(int(fields[1])-1)

	return chr_sizes

def get_ref_centromeres(ref_name):
	centromere_dict = {} 
	with open(AA_REPO + "/" + ref_name + "/" + ref_name + "_centromere.bed") as infile:
		for line in infile:
			fields = line.rstrip().rsplit("\t")
			if fields[0] not in centromere_dict:
				centromere_dict[fields[0]] = (fields[1],fields[2])

			else:
				pmin = min(int(centromere_dict[fields[0]][0]),int(fields[1]))
				pmax = max(int(centromere_dict[fields[0]][1]),int(fields[2]))
				#pad with 15kb
				centromere_dict[fields[0]] = (str(pmin-15000),str(pmax+15000))

	return centromere_dict

### MAIN ###
if __name__ == '__main__':
    # Parses the command line arguments
	parser = argparse.ArgumentParser(description="A simple pipeline wrapper for AmpliconArchitect, invoking alignment, variant calling, and CNV calling prior to AA. The CNV calling is necesary for running AA")
	parser.add_argument("-o", "--output_directory", help="output directory names (will create if not already created)")
	parser.add_argument("-s", "--sample_name", help="sample name", required=True)
	parser.add_argument("--canvas_data_dir",help="Path to folder with required Canvas reference reference files",required=True)
	parser.add_argument("-t","--nthreads",help="Number of threads to use in BWA and AA",required=True)
	parser.add_argument("--run_AA", help="Run AA after all files prepared. Default off.", action='store_true')
	parser.add_argument("--ref", help="Reference genome version. Only Hg19 currently supported.",choices=["hg19","GRCh37","hg38"],default="hg19")
	parser.add_argument("--vcf", help= "VCF (in Canvas format, i.e., \"PASS\" in filter field, AD field as 4th entry of FORMAT field). When supplied with \"--sorted_bam\", pipeline will start from Canvas CNV stage.")
	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument("--sorted_bam", help= "Sorted BAM file (aligned to AA/Canvas compatible reference)")
	group.add_argument("--fastqs", help="Fastq files (r1.fq r2.fq)", nargs=2)

	args = parser.parse_args()

	#Add a argument to specify Hg19 or GRCh37

	#Check if AA_REPO set, print error and quit if not
	try:
		AA_REPO = os.environ['AA_DATA_REPO']

	except KeyError:
		sys.stderr.write("AA_DATA_REPO bash variable not found. AmpliconArchitect may not be properly installed.\n")
		sys.exit()
	
	try:
		AA_SRC = os.environ['AA_SRC']
	
	except KeyError:
		sys.stderr.write("AA_SRC bash variable not found. AmpliconArchitect may not be properly installed.\n")
		sys.exit()

	####MUST CHANGE REF IF NOT USING HG19
	##UPDATE TO USE AA_DATA_REPO
	if args.ref == "hg19":
 		ref = AA_REPO + "/hg19/hg19full.fa"
 		ref_genome_size_file = AA_REPO + "/hg19/hg19full.fa.fai"
 		removed_regions_bed = AA_REPO + "/hg19/hg19_merged_centromeres_conserved_sorted.bed"

 	else:
 		print("Other reference versions currently unsupported.")
 		sys.exit()

	if not os.path.exists(args.canvas_data_dir):
		sys.stderr.write("Could not locate Canvas data repo folder")
		sys.exit()
 	
 	#check for the bed file of regions to ignore (centromeres, low complexity)
 	ploidy_vcf = AA_REPO + "/" + args.ref + "/dummy_ploidy.vcf"
 	merged_vcf_file = args.vcf
 	if not os.path.isfile(removed_regions_bed):
 		sys.stderr.write("Could not locate " + removed_regions_bed + "\n")
 		sys.exit()

 	if not args.output_directory:
 		args.output_directory = os.getcwd()

 	if not os.path.exists(args.output_directory):
 		os.mkdir(args.output_directory)

 	freebayes_output_directory = args.output_directory + "/freebayes_vcfs/"
 	if not os.path.exists(freebayes_output_directory):
 		os.mkdir(freebayes_output_directory)

 	canvas_output_directory = args.output_directory + "/canvas_output/"
 	if not os.path.exists(canvas_output_directory):
 		os.mkdir(canvas_output_directory)
 	else:
 		#prompt user to clear old results
 		user_input = raw_input("Canvas folder already exists.\n Clear old Canvas results? Highly recommended. Canvas re-uses old results, even if they cause an error. (y/n): ")
 		if user_input.lower() == "y" or user_input.lower() == "yes":
 			print("Clearing results")
 			call("rm -rf {}/TempCNV*".format(canvas_output_directory),shell=True)
 			call("rm -rf {}/Logging".format(canvas_output_directory),shell=True)
 			call("rm -rf {}/Checkpoints".format(canvas_output_directory),shell=True)
 		else:
 			print("NOT CLEARING OLD CANVAS OUTPUT. THIS IS NOT RECOMMENDED.")


 	sname =  args.sample_name
 	outdir = args.output_directory + "/"

 	print("Running PrepareAA on sample: " + sname)

 	#Check if Fastqs provided
 	if args.fastqs:
		#Run BWA
		fastqs = " ".join(args.fastqs)
		print("Running pipeline on " + fastqs)
		args.sorted_bam = run_bwa(ref,fastqs,outdir,sname, args.nthreads)

	if not args.sorted_bam + ".bai":
		call("samtools index " + args.sored_bam, shell=True)

	centromere_dict = get_ref_centromeres(args.ref)

	#chunk the genome by chr
	chr_sizes = get_ref_sizes(ref_genome_size_file)
	regions = []
	for key,value in chr_sizes.iteritems():
		try:
			cent_tup = centromere_dict[key]
			regions.append((key,"0-" + cent_tup[0],"p"))
			regions.append((key,cent_tup[1] + "-" + value,"q"))
		except KeyError:
			regions.append((key,"0-" + value,""))
	
	if not merged_vcf_file:
		#Run FreeBayes, one instance per chromosome
		threadL = []
		for i in range(int(args.nthreads)):
			threadL.append(workerThread(i, run_freebayes, ref, args.sorted_bam, freebayes_output_directory, sname, args.nthreads, regions))
			threadL[i].start()

		for t in threadL:
			t.join()

		#make a list of vcf files
		vcf_files = [freebayes_output_directory + x for x in os.listdir(freebayes_output_directory) if x.endswith(".vcf.gz")]

		#MERGE VCFs
		merged_vcf_file = merge_and_filter_vcfs(chr_sizes.keys(),vcf_files,outdir, sname)

	else:
		print("Using " + merged_vcf_file + " for Canvas CNV step. Improper formatting of VCF can causes errors.")

	#Run Canvas
	run_canvas(args.sorted_bam, merged_vcf_file, canvas_output_directory, args.canvas_data_dir, removed_regions_bed,sname,ref)

	#Convert Canvas output to seeds
	amplified_interval_bed = convert_canvas_cnv_to_seeds(canvas_output_directory,args.sorted_bam,outdir,sname)

	#Run AA
	if args.run_AA:
		if not os.path.exists(outdir + "/AA_results"):
 			os.mkdir(outdir + "/AA_results")

 		#run_AA(amplified_interval_bed, sorted_bam, AA_outdir, sname)
 		run_AA(amplified_interval_bed, args.sorted_bam, outdir + "/AA_results", sname)
		

	print("Completed\n")