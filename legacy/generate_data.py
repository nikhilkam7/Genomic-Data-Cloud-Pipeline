import csv
import random

# File name we want to create
OUTPUT_FILE = "variants.csv"

# Sample headers for our biological dataset
headers = ["Sample_ID", "Chromosome", "Position", "Read_Depth", "Quality_Score", "Variant_Type"]

chromosomes = ["chr1", "chr2", "chr3", "chrX"]
variant_types = ["SNP", "Insertion", "Deletion"]

# Generate 100 sample records
data = []
for i in range(1, 101):
    sample_id = f"SMP_{i:03d}"
    chrom = random.choice(chromosomes)
    pos = random.randint(10000, 999999)
    depth = random.randint(5, 100)           # Read depth between 5x and 100x
    quality = round(random.uniform(10.0, 99.0), 2) # Quality score between 10.0 and 99.0
    v_type = random.choice(variant_types)
    
    data.append([sample_id, chrom, pos, depth, quality, v_type])

# Write the data to a CSV file
with open(OUTPUT_FILE, mode="w", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(headers)   # Write header row
    writer.writerows(data)     # Write data rows

print(f"✅ Successfully created {OUTPUT_FILE} with 100 variant records!")